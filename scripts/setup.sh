#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo '请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
command -v node >/dev/null || { echo '请先安装 Node.js 22 或更高版本。'; exit 1; }
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-local.txt
[ -x .venv-mlx/bin/python ] || uv venv --python 3.12 .venv-mlx
uv pip install --python .venv-mlx/bin/python -r requirements-mlx.txt
mkdir -p .runtime .cache/swift
HF_HOME="$PWD/.cache/huggingface" .venv-mlx/bin/python scripts/download_assets.py "$@"
swiftc -parse-as-library -O -module-cache-path .cache/swift native/WindowObserver.swift -o .runtime/window-observer
npm --prefix desktop ci --cache "$PWD/.cache/npm"
npm --prefix desktop run build
echo '完成。运行 .venv/bin/python scripts/run_local.py；可选 --model 27b --voice mao。'
