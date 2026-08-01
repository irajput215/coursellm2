#!/bin/dash

echo "Starting local Redis service..."
brew services start redis

echo "Starting Dramatiq background worker..."
# Redirect all output to /dev/null and fully detach
uv run dramatiq workers.orchestrator --processes 1 --threads 4 > /dev/null 2>&1 &
WORKER_PID=$!

# Give Dramatiq time to initialize (IMPORTANT!)
sleep 3

echo "Starting FastAPI server..."
# Don't trap - let Ctrl+C kill both naturally
uv run uvicorn main:app --reload --reload-exclude ".venv/*"

# This line only runs after uvicorn stops
echo "Stopping Dramatiq background worker..."
kill $WORKER_PID 2>/dev/null