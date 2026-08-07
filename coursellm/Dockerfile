# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Install system dependencies required for ML libraries and Postgres
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.4.15 /uv /bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
# We use --system so we don't need to manage a virtual environment in Docker
RUN uv sync --frozen

# Copy the rest of the application code
COPY . .

# Set up the path to include the virtual environment created by uv sync
ENV PATH="/app/.venv/bin:$PATH"

# Default command (overridden by render.yaml for worker vs web)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
