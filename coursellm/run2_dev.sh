#!/bin/dash

echo "Starting local Redis service..."
brew services start redis

echo "Starting Dramatiq background worker..."
# Use a named pipe or log file to verify it's actually running
UVICORN_LOG="/tmp/uvicorn_startup.log"
uv run dramatiq workers.orchestrator --processes 1 --threads 4 > /tmp/dramatiq.log 2>&1 &
WORKER_PID=$!

# Wait for Dramatiq to be ready (check logs)
echo "Waiting for Dramatiq to initialize..."
timeout=10
while [ $timeout -gt 0 ]; do
    if grep -q "dramatiq worker" /tmp/dramatiq.log 2>/dev/null; then
        echo "Dramatiq ready"
        break
    fi
    sleep 1
    timeout=$((timeout - 1))
done

echo "Starting FastAPI server..."
# Pass the worker PID to uvicorn process group
# uv run uvicorn main:app --reload --reload-exclude ".venv/*" --reload-exclude "*.pyc"
uv run uvicorn main:app

# Cleanup
echo "Stopping Dramatiq background worker..."
kill $WORKER_PID 2>/dev/null
