#!/bin/bash
# start.sh
# This script starts the Dramatiq worker in the background and the FastAPI server in the foreground.

echo "Starting Dramatiq background worker..."
dramatiq workers.orchestrator --processes 1 --threads 4 &

echo "Starting FastAPI server on port 7860..."
uvicorn main:app --host 0.0.0.0 --port 7860
