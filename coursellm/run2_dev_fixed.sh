#!/bin/dash

# Help with PyTorch on Mac
export PYTORCH_ENABLE_MPS_FALLBACK=1
export TOKENIZERS_PARALLELISM=false

echo "Starting local Redis service..."
brew services start redis

echo "Starting Dramatiq background worker..."
uv run dramatiq workers.orchestrator --processes 1 --threads 4 > /tmp/dramatiq.log 2>&1 &
WORKER_PID=$!

sleep 3

echo "Starting FastAPI server..."
uv run uvicorn main:app --reload --reload-exclude ".venv/*"

kill $WORKER_PID 2>/dev/null
