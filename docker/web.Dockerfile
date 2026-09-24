# CourseLLM web client — static asset image.
#
# There is no server-side runtime for the frontend and no Vercel: the build
# emits static assets into `apps/web/dist`, which this image serves with nginx.
# In the AWS topology the same `dist/` directory is synced to S3 and served by
# CloudFront, with `/api/*` routed to the API service (a separate origin).
#
# Build from the repository root:
#
#     docker build -f docker/web.Dockerfile -t coursellm-web .
#
# Point the bundle at a different API origin at build time:
#
#     docker build -f docker/web.Dockerfile \
#       --build-arg VITE_API_BASE_URL=https://api.example.com -t coursellm-web .

# ---- build ------------------------------------------------------------------
FROM node:24-alpine AS build
WORKDIR /app

# Dependency manifests first so the npm layer is cached across source edits.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY apps/web/ ./

# Vite inlines every VITE_* value into the bundle, so nothing secret may be
# passed here. The API is authenticated at runtime with short-lived tokens.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# ---- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine AS serve

COPY --from=build /app/dist /usr/share/nginx/html

RUN cat > /etc/nginx/conf.d/default.conf <<'NGINX'
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    gzip on;
    gzip_min_length 1024;
    gzip_types text/css application/javascript application/json image/svg+xml;

    # Hashed asset filenames are content-addressed, so they can be cached hard.
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # The shell must be revalidated, otherwise a deploy is invisible.
    location = /index.html {
        add_header Cache-Control "no-store";
    }

    # Container liveness for the orchestrator; it is not the API's /healthz.
    location = /healthz {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    # Single-page application fallback: unknown paths render the 404 route
    # rather than nginx's own error page.
    location / {
        try_files $uri $uri/ /index.html;
    }
}
NGINX

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -q -O - http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1
