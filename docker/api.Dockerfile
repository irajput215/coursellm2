# CourseLLM API image.
#
# Multi-stage. The compiler, the headers and the pip cache exist only in the
# `builder` stage; the `runtime` stage is a slim Python base plus a copied
# virtualenv, and cannot build a wheel. See
# docs/architecture/deployment.md sections 3.1 and 7.
#
# Build from the repository root (the build context must be the repository root,
# because the image needs apps/api, prompts/ and scripts/):
#
#     docker build -f docker/api.Dockerfile -t coursellm-api .
#
# The default install is the core dependency set plus `docx`. The `embeddings`
# and `rerank` extras pull PyTorch (hundreds of MB to >1 GB) and are opt-in, so
# that the image CI and the default compose stack use stays small:
#
#     docker build -f docker/api.Dockerfile \
#       --build-arg INSTALL_EXTRAS=embeddings,rerank -t coursellm-api .
#
# See docker/README.md for the trade-off and the expected sizes.
#
# Both bases are pinned by tag AND by the multi-architecture manifest-list
# digest, so an image that changes without a commit is not possible. The digests
# were resolved from the registry at implementation time; to refresh one:
#
#     docker buildx imagetools inspect python:3.13-slim

# ---- builder ----------------------------------------------------------------
FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build-only toolchain. Anything that has to compile from source does so here;
# none of this layer is copied into the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# A virtualenv at a fixed path, so the runtime stage copies exactly one tree and
# never needs the build backend.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# The package is installed *editable* deliberately. coursellm.core.config
# derives REPO_ROOT from the source file's location, and the application resolves
# both `prompts/` and the local object store (`uploads/object_store`) relative to
# it. Keeping the source at /app/apps/api/src makes REPO_ROOT resolve to /app in
# the container exactly as it does on a workstation. A normal site-packages
# install would point REPO_ROOT at the virtualenv and silently relocate both.
COPY apps/api/pyproject.toml apps/api/README.md /app/apps/api/
COPY apps/api/src /app/apps/api/src

# `docx` is always installed: it is small and the ingestion pipeline advertises
# .docx in ALLOWED_UPLOAD_EXTENSIONS. INSTALL_EXTRAS is empty by default; the
# only reason to set it is to run the embedding or reranking models in-process.
ARG INSTALL_EXTRAS=""
RUN extras="docx"; \
    if [ -n "$INSTALL_EXTRAS" ]; then extras="docx,$INSTALL_EXTRAS"; fi; \
    python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e "/app/apps/api[$extras]"

# ---- runtime ----------------------------------------------------------------
FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS runtime

LABEL org.opencontainers.image.title="coursellm-api" \
      org.opencontainers.image.description="CourseLLM API — FastAPI, retrieval, agents" \
      org.opencontainers.image.source="https://github.com/irajput215/coursellm2" \
      org.opencontainers.image.licenses="MIT"

# psql(1) is the only package added to the runtime stage. It is used by the
# one-shot `migrate` service to apply scripts/bootstrap_db.sql, and by the
# compose readiness check that proves the pgvector extension exists for the
# application role. It is a database client, not a compiler: this stage still
# has no build toolchain.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PROMPTS_DIR=/app/prompts

# Fixed high uid. A random uid cannot be granted write access to a mounted
# volume ahead of time; a fixed one is reproducible across hosts and is what
# makes the `uploads` volume work.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

# The migrate service runs `alembic upgrade head` from here (alembic.ini uses
# `script_location = alembic`, relative to the working directory).
WORKDIR /app/apps/api

COPY --from=builder /opt/venv /opt/venv
# Same source tree as the builder used, at the same absolute path, so the
# editable install's .pth entry (/app/apps/api/src) resolves.
COPY --from=builder /app/apps/api/src /app/apps/api/src
COPY apps/api/alembic /app/apps/api/alembic
COPY apps/api/alembic.ini /app/apps/api/alembic.ini
COPY prompts /app/prompts
COPY scripts/bootstrap_db.sql /app/scripts/bootstrap_db.sql

# The only paths the process writes to. `uploads` is a named volume in compose;
# creating it here fixes the ownership the fresh volume inherits.
RUN mkdir -p /app/uploads/object_store /app/scratch \
    && chown -R app:app /app

USER 10001:10001

EXPOSE 8000

# Liveness only. /readyz is dependency-aware, and a dependency-aware HEALTHCHECK
# restarts a healthy process every time the database hiccups — a restart loop
# caused by a transient dependency failure. Docker-level liveness and
# orchestrator-level readiness are separate signals, so /readyz is left to the
# orchestrator (compose overrides this healthcheck with it).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

# Exec form: the process is PID 1 and receives SIGTERM directly, so uvicorn's
# graceful shutdown runs instead of being skipped behind a shell.
ENTRYPOINT ["uvicorn", "coursellm.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
