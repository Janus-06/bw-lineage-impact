#!/usr/bin/env bash
set -euo pipefail

uv run pytest -q
uv run ruff check .
uv run mypy src
npm --prefix web ci
npm --prefix web run build
