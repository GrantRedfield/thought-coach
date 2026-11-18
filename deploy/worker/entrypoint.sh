#!/bin/sh
# Start the speech tagger, wait until it answers, then run Thought Coach with the given arguments
# (e.g. "review recording.json --out out --games games", or the queue worker).
set -e
ollama serve >/tmp/ollama.log 2>&1 &
for i in $(seq 1 30); do
  curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
exec python -m thoughtcoach "$@"
