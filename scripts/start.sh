#!/usr/bin/env sh
cd "$(dirname "$0")/.." || exit 1
if [ ! -f .env ]; then
  echo "[KnowLens] .env not found. Copy .env.example to .env and fill in the model settings."
  exit 1
fi
echo "[KnowLens] starting … http://127.0.0.1:8633"
exec python run.py