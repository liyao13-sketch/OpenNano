#!/bin/bash
# OpenNano 开发启动:后端(8000) + 前端(5173)
set -e
cd "$(dirname "$0")"
echo "▶ 后端: http://localhost:8000  (Ctrl+C 退出后前端也停)"
(cd server && .venv/bin/uvicorn main:app --port 8000 --reload) &
BACK=$!
trap "kill $BACK 2>/dev/null" EXIT
sleep 1
cd web && npm run dev
