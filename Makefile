.PHONY: install init admin seed run test lint security-check clean

PYTHON ?= python3.12

install:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

init:
	.venv/bin/python -m app.cli init-db

admin:
	.venv/bin/python -m app.cli create-admin

seed:
	.venv/bin/python -m app.cli seed-demo

run:
	.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-server-header --ws-max-size 4096 --ws-max-queue 16 --ws-per-message-deflate false --limit-concurrency 100

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

security-check:
	.venv/bin/bandit -q -r app -x tests
	.venv/bin/pip-audit -r requirements.txt

clean:
	find app tests -type d -name __pycache__ -prune -exec rm -r {} +
