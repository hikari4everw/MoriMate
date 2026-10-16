#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python scripts/run_local.py --model 27b --voice mao --web
