import json
import os
import pathlib
import queue
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional
import time
import shutil  # Add this import at the beginning of your file
from datetime import datetime

import agentops
import colorama
import ollama
import threading
from asciitree import LeftAligned
from asciitree.drawing import BOX_LIGHT, BoxStyle
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from llama_index.core import SimpleDirectoryReader
from pydantic import BaseModel
from termcolor import colored
from watchdog.observers import Observer

from src.loader import get_dir_summaries
from src.tree_generator import create_file_tree
from src.watch_utils import Handler
from src.watch_utils import create_file_tree as create_watch_file_tree

from dotenv import load_dotenv
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()  # 'text' or 'json'

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Optional extras
        for extra_key in ["session_id", "request_id"]:
            if hasattr(record, extra_key):
                payload[extra_key] = getattr(record, extra_key)
        return json.dumps(payload, ensure_ascii=False)

class AccessLogFormatter(logging.Formatter):
    """Custom formatter for uvicorn access logs that outputs JSON when LOG_FORMAT=json."""
    def format(self, record: logging.LogRecord) -> str:
        if LOG_FORMAT == "json":
            # Parse the default uvicorn access log message for structured data
            msg = record.getMessage()
            payload = {
                "ts": datetime.utcnow().isoformat() + "Z",
                "level": record.levelname,
                "logger": record.name,
                "msg": msg,
                "type": "access",
            }
            # Extract structured fields if available from record
            for attr in ["client_addr", "method", "full_path", "http_version", "status_code"]:
                if hasattr(record, attr):
                    payload[attr] = getattr(record, attr)
            return json.dumps(payload, ensure_ascii=False)
        else:
            # Use default uvicorn access log format
            return super().format(record)

handler = logging.StreamHandler()
if LOG_FORMAT == "json":
    handler.setFormatter(JsonFormatter())
else:
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

logger = logging.getLogger("llama-fs")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.handlers = [handler]
logger.propagate = False

# Configure uvicorn access logger
access_logger = logging.getLogger("uvicorn.access")
access_logger.handlers = []  # Clear default handlers
access_handler = logging.StreamHandler()
if LOG_FORMAT == "json":
    access_handler.setFormatter(AccessLogFormatter())
else:
    # Use uvicorn's default format
    access_handler.setFormatter(logging.Formatter('%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'))
access_logger.addHandler(access_handler)
access_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
access_logger.propagate = False

agentops.init(tags=["llama-fs"],
              auto_start_session=False)


class Request(BaseModel):
    path: Optional[str] = None
    instruction: Optional[str] = None
    incognito: Optional[bool] = False


class CommitRequest(BaseModel):
    base_path: str
    src_path: str  # Relative to base_path
    dst_path: str  # Relative to base_path


app = FastAPI()
logger.info("FastAPI app initialized", extra={"event": "startup"})

origins = [
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Or restrict to ['POST', 'GET', etc.]
    allow_headers=["*"],
)


@app.get("/")
async def root():
    logger.info("root endpoint hit")
    return {"message": "Hello World"}


@app.get("/health")
async def health():
    """Basic health/status endpoint.

    Returns:
        dict: status info including env variable presence and optional service availability.
    """
    groq_key_present = bool(os.environ.get("GROQ_API_KEY"))
    ollama_available = False
    try:
        # Lightweight availability check (list models) -- will fail fast if daemon not running
        ollama.list()
        ollama_available = True
    except Exception:
        pass
    data = {
        "status": "ok",
        "groq_api_key": groq_key_present,
        "ollama_available": ollama_available,
        "time": time.time(),
    }
    logger.info("health check", extra={"groq_api_key": groq_key_present, "ollama_available": ollama_available})
    return data


@app.post("/batch")
async def batch(request: Request):
    logger.info("batch request received", extra={"path": request.path})
    session = agentops.start_session(tags=["LlamaFS"])
    path = request.path
    if not os.path.exists(path):
        raise HTTPException(
            status_code=400, detail="Path does not exist in filesystem")

    summaries = await get_dir_summaries(path)
    # Get file tree
    files = create_file_tree(summaries, session)

    # Recursively create dictionary from file paths
    tree = {}
    for file in files:
        parts = Path(file["dst_path"]).parts
        current = tree
        for part in parts:
            current = current.setdefault(part, {})

    tree = {path: tree}

    tr = LeftAligned(draw=BoxStyle(gfx=BOX_LIGHT, horiz_len=1))
    print(tr(tree))

    # Prepend base path to dst_path
    for file in files:
        # file["dst_path"] = os.path.join(path, file["dst_path"])
        file["summary"] = summaries[files.index(file)]["summary"]

    agentops.end_session(
        "Success", end_state_reason="Reorganized directory structure")
    logger.info("batch completed", extra={"file_count": len(files)})
    return files


@app.post("/watch")
async def watch(request: Request):
    path = request.path
    logger.info("watch request received", extra={"path": path})
    if not os.path.exists(path):
        raise HTTPException(
            status_code=400, detail="Path does not exist in filesystem")

    response_queue = queue.Queue()

    observer = Observer()
    event_handler = Handler(path, create_watch_file_tree, response_queue)
    await event_handler.set_summaries()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()

    # background_tasks.add_task(observer.start)

    def stream():
        while True:
            response = response_queue.get()
            yield json.dumps(response) + "\n"
            # yield json.dumps({"status": "watching"}) + "\n"
            # time.sleep(5)

    return StreamingResponse(stream())


@app.post("/commit")
async def commit(request: CommitRequest):
    logger.info("commit request", extra={"base_path": request.base_path, "src_path": request.src_path, "dst_path": request.dst_path})

    src = os.path.join(request.base_path, request.src_path)
    dst = os.path.join(request.base_path, request.dst_path)

    if not os.path.exists(src):
        raise HTTPException(
            status_code=400, detail="Source path does not exist in filesystem"
        )

    # Ensure the destination directory exists
    dst_directory = os.path.dirname(dst)
    os.makedirs(dst_directory, exist_ok=True)

    try:
        # If src is a file and dst is a directory, move the file into dst with the original filename.
        if os.path.isfile(src) and os.path.isdir(dst):
            shutil.move(src, os.path.join(dst, os.path.basename(src)))
        else:
            shutil.move(src, dst)
        logger.info("commit moved", extra={"src": src, "dst": dst})
    except Exception as e:
        logger.error("commit error", extra={"error": str(e)})
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while moving the resource: {e}"
        )

    return {"message": "Commit successful"}
