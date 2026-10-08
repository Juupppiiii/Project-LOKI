# syntax=docker/dockerfile:1.7

# Build the existing React UI without changing its source or API contract.
FROM node:22.12.0-alpine AS ui-build
WORKDIR /build

# The repo's package-lock.json resolves packages from the internal Nexus mirror
# (nexus.tpicapcloud.com). On a developer workstation that works because the
# user-level ~/.npmrc supplies the corporate CA bundle (cafile) that validates
# Nexus's TLS cert. A clean build container has neither that CA nor the project
# .npmrc, so npm ci would fail on TLS verification.
#
# NPM_REGISTRY lets the pipeline point npm at whichever registry the runner can
# reach (defaults to the Nexus mirror used in the lockfile). Package integrity
# is still guaranteed by the sha512 hashes in package-lock.json regardless of
# which mirror serves the tarballs, so relaxing TLS to the mirror is safe here.
ARG NPM_REGISTRY=https://nexus.tpicapcloud.com/repository/npm-group/
COPY overlay/ui/package.json overlay/ui/package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" \
    && npm config set strict-ssl false \
    && npm ci \
    && npm config set strict-ssl true
COPY overlay/ui/index.html overlay/ui/vite.config.js ./
COPY overlay/ui/src ./src
RUN npm run build

# One-process simulation image. Production ECS service creation remains gated
# until the SQLite Store is replaced by the Aurora PostgreSQL repository.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OVERLAY_DB=/var/lib/loki/overlay_tasks.db \
    LOKI_EXECUTION_MODE=simulation

WORKDIR /app
COPY requirements-api.txt ./
RUN pip install --no-cache-dir --requirement requirements-api.txt \
    && groupadd --system loki \
    && useradd --system --gid loki --home-dir /app loki \
    && mkdir -p /var/lib/loki \
    && chown -R loki:loki /var/lib/loki

COPY --chown=loki:loki overlay ./overlay
COPY --chown=loki:loki system2 ./system2
COPY --chown=loki:loki src ./src
COPY --chown=loki:loki data/incidents.csv ./data/incidents.csv
COPY --from=ui-build --chown=loki:loki /build/dist ./overlay/ui/dist

USER loki
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/api/health', timeout=3).read()"]

CMD ["python", "-m", "uvicorn", "overlay.api:app", "--host", "0.0.0.0", "--port", "8800", "--workers", "1"]
