#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
.venv/bin/python scripts/codex_model.py local
result=$?
printf '\n按回车关闭此窗口。'
read -r reply
exit "$result"
