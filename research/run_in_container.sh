#!/bin/sh
# Research lane: run research/measure.py <cmd> in a throwaway container on a COPY of the owner's database
# (appdata mounted read-only, library mounted read-only, no network). Usage: run_in_container.sh timing|pending|equiv
set -e
cd "$(dirname "$0")/.."
cmd="$1"
tar -c mangalist research | docker run --rm -i --network none --tmpfs /tmp:rw,size=3g,exec -u 99:100 \
  -v /mnt/user/appdata/mangalist:/live:ro -v /mnt/user:/data:ro \
  -e HOME=/tmp -e PYTHONPATH=/tmp/n -e MANGALIST_DATA_DIR=/tmp/c/c -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint sh mangalist:2026.10.29 -c "mkdir -p /tmp/n /tmp/c/c && tar -x -C /tmp/n && cp /live/mangalist.db* /tmp/c/c/ && \
  /opt/mangalist/venv/bin/python -c \"import sqlite3;print('integrity',sqlite3.connect('/tmp/c/c/mangalist.db').execute('pragma integrity_check').fetchone()[0])\" && \
  /opt/mangalist/venv/bin/python /tmp/n/research/measure.py $cmd"
