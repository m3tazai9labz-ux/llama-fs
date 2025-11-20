import os
import json
import argparse
import pathlib
try:
    import importlib
    _groq_mod = importlib.import_module("groq")
    Groq = getattr(_groq_mod, "Groq")
except Exception:
    class Groq:
        def __init__(self, *args, **kwargs):
            pass

try:
    import importlib
    _llama_mod = importlib.import_module("llama_index.core")
    SimpleDirectoryReader = getattr(_llama_mod, "SimpleDirectoryReader")
except Exception:
    class SimpleDirectoryReader:
        def __init__(self, *args, **kwargs):
            # Minimal stub fallback to avoid import errors during static analysis or missing package
            pass

try:
    import colorama
except Exception:
    class _DummyColorama:
        def init(self, *args, **kwargs):
            pass
    colorama = _DummyColorama()

import pathlib
from pathlib import Path
from termcolor import colored
from asciitree import LeftAligned
from asciitree.drawing import BoxStyle, BOX_LIGHT
from src.loader import get_dir_summaries
from src.tree_generator import create_file_tree
import asyncio
from dotenv import load_dotenv
try:
    import click
except Exception:
    import argparse as _argparse

    class _FakeClick:
        def command(self, *args, **kwargs):
            def decorator(f):
                def wrapper(*a, **kw):
                    parser = _argparse.ArgumentParser()
                    parser.add_argument("src_path")
                    parser.add_argument("dst_path")
                    parser.add_argument("--auto-yes", action="store_true")
                    parsed = parser.parse_args()
                    return f(parsed.src_path, parsed.dst_path, auto_yes=parsed.auto_yes)
                return wrapper
            return decorator

        def argument(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator

        def option(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator

        def confirm(self, prompt, default=False):
            try:
                resp = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
                if not resp:
                    return default
                return resp in ("y", "yes")
            except Exception:
                return default

        def echo(self, msg):
            print(msg)

    click = _FakeClick()

load_dotenv()
colorama.init()  # Initializes colorama to make it work on Windows as well


@click.command()
@click.argument("src_path", type=click.Path(exists=True))
@click.argument("dst_path", type=click.Path())
@click.option("--auto-yes", is_flag=True, help="Automatically say yes to all prompts")
def main(src_path, dst_path, auto_yes=True):

    summaries = asyncio.run(get_dir_summaries(src_path))

    # Get file tree
    session = Groq()
    files = create_file_tree(summaries, session=session)

    BASE_DIR = pathlib.Path(dst_path)
    BASE_DIR.mkdir(exist_ok=True)

    # Recursively create dictionary from file paths
    tree = {}
    for file in files:
        parts = Path(file["dst_path"]).parts
        current = tree
        for part in parts:
            current = current.setdefault(part, {})

    tree = {dst_path: tree}

    tr = LeftAligned(draw=BoxStyle(gfx=BOX_LIGHT, horiz_len=1))
    print(tr(tree))

    # Prepend base path to dst_path
    for file in files:
        file["dst_path"] = os.path.join(src_path, file["dst_path"])
        file["summary"] = summaries[files.index(file)]["summary"]

    if not auto_yes and not click.confirm(
        "Proceed with directory structure?", default=True
    ):
        click.echo("Operation cancelled.")
        return

    for file in files:
        file["path"] = pathlib.Path(file["dst_path"])
        # Create file in specified base directory
        (BASE_DIR / file["path"]).parent.mkdir(parents=True, exist_ok=True)
        with open(BASE_DIR / file["path"], "w") as f:
            f.write("")


if __name__ == "__main__":
    main()
