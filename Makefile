PYTHON := python
PIP := $(PYTHON) -m pip

.PHONY: install run

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run:
	$(PYTHON) -m uvicorn server:app --reload --host 127.0.0.1 --port 8000
