import subprocess
import uvicorn
from main import app

# Start Dramatiq worker in background
print("Starting Dramatiq background worker...")
subprocess.Popen(["dramatiq", "workers.orchestrator", "--processes", "1", "--threads", "4"])

# Start FastAPI
if __name__ == "__main__":
    print("Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=7860)
