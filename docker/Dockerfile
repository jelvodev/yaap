# Build context: the repository root (see docker-compose.build.yml).
# Base images are pinned by digest, with the tag kept for reading; CONTRIBUTING.md says how to update one.

FROM node:24-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YAAP_DATA_DIR=/data \
    YAAP_STATIC_DIR=/app/static \
    YAAP_PREVIEW_BIND=0.0.0.0 \
    CLAUDE_CONFIG_DIR=/data/claude-code \
    CODEX_HOME=/data/codex \
    YAAP_TOOLS_DIR=/data/tools \
    YAAP_TOOLS_SOURCE=/app/tools \
    PATH="/data/tools/node_modules/.bin:${PATH}" \
    DISABLE_AUTOUPDATER=1

# git, curl and the certificates for HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# OCR for scanned PDFs and images added as knowledge or attachments: Tesseract, Dutch and English.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --version

RUN useradd --create-home --uid 1000 yaap \
    && mkdir -p /data \
    && chown yaap:yaap /data
WORKDIR /app
# Every package, also the indirect ones, at the version and with the hashes of the lock file (CONTRIBUTING.md).
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock
# Node.js and npm for installing the CLIs, taken from the same image as the frontend build. The agent's commands, MCP
# servers over stdio and its browser run in the runner of their environment (docker/sandbox), not in this image.
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# The two files that pin Claude Code and Codex (versions and integrity hashes). YAAP installs both from them into
# /data/tools at the first sign-in, so neither CLI is part of this image.
COPY tools/package.json tools/package-lock.json ./tools/

COPY backend/app ./app
COPY --from=frontend /build/dist ./static
# Which commit and channel this build is, shown by GET /api/v1/version; last, so a new value rebuilds nothing before it.
ARG YAAP_COMMIT=
ARG YAAP_CHANNEL=
ENV YAAP_COMMIT=${YAAP_COMMIT} YAAP_CHANNEL=${YAAP_CHANNEL}
USER yaap
# 8081: the preview of a project's app (YAAP_PREVIEW_PORT), a second listener the app starts itself.
EXPOSE 8080 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
