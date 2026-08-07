# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Create user to run as non-root (Hugging Face Spaces requirement)
RUN useradd -m -u 1000 user

# Install system dependencies required for ML libraries and Postgres
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.4.15 /uv /bin/uv

# Set working directory and change ownership
WORKDIR /app
RUN chown -R user:user /app

# Switch to the non-root user
USER user

# Copy dependency files
COPY --chown=user:user pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen

# Copy the rest of the application code
COPY --chown=user:user . .

# Set up the path to include the virtual environment created by uv sync
ENV PATH="/app/.venv/bin:$PATH"

# Expose port 7860 for Hugging Face Spaces
EXPOSE 7860

# Run the start script to boot both Dramatiq and FastAPI
CMD ["./start.sh"]
