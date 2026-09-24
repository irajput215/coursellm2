# CourseLLM web client — static asset image.
#
# There is no server-side runtime for the frontend and no Vercel: the build
# emits static assets into `apps/web/dist`, which this image serves with nginx.
# In the AWS topology the same `dist/` directory is synced to S3 and served by
# CloudFront, with `/api/*` routed to the API service (a separate origin).
#
# This image is the delivery mechanism for the real React client in apps/web.
# It builds that client; it does not invent one.
#
# Build from the repository root (the context must be the repository root, so
# the image can read apps/web and docker/nginx.conf):
#
#     docker build -f docker/web.Dockerfile -t coursellm-web .
#
# Point the bundle at a different API origin at build time:
#
#     docker build -f docker/web.Dockerfile \
#       --build-arg VITE_API_BASE_URL=https://api.example.com -t coursellm-web .
#
# The nginx server block lives in docker/nginx.conf so that the SPA fallback,
# security headers and cache policy are reviewable in one place (and reusable
# for a self-hosted deployment).

# ---- build ------------------------------------------------------------------
# Pinned by tag AND by the multi-architecture manifest-list digest.
FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4 AS build

WORKDIR /app

# Dependency manifests first so the npm layer is cached across source edits.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY apps/web/ ./

# Vite inlines every VITE_* value into the bundle, so nothing secret may be
# passed here. The API is authenticated at runtime with short-lived tokens.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# `npm run build` runs the type-check and then `vite build` into apps/web/dist.
RUN npm run build

# ---- serve ------------------------------------------------------------------
# Pinned by tag AND by the multi-architecture manifest-list digest.
#
# `nginx-unprivileged` is the same nginx built to run as the non-root `nginx`
# user (uid 101) with its caches and pid file already redirected to writable
# paths. It listens on 8080 because a non-root process cannot bind port 80 —
# which is why docker/nginx.conf says `listen 8080` and compose maps the host
# port onto 8080.
FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6 AS serve

LABEL org.opencontainers.image.title="coursellm-web" \
      org.opencontainers.image.description="CourseLLM web client — static assets served by nginx" \
      org.opencontainers.image.source="https://github.com/irajput215/coursellm2" \
      org.opencontainers.image.licenses="MIT"

# Swapping in a different build output is a one-line change: point this COPY at
# another directory (or a prebuilt artefact) and nothing else moves.
#
# The base image already switches to uid 101. Switch back to root only for the
# two COPY operations, so they cannot depend on the base image's ownership
# layout, then drop to the non-root user for the runtime.
USER 0
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

USER 101

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O - http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
