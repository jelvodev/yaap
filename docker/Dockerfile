# Build context: the repository root (see docker-compose.yml).
# Base images are pinned by digest, with the tag kept for reading; CONTRIBUTING.md says how to update one.

FROM node:24-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# The embedding model for semantic search: BAAI/bge-m3 as ONNX, at a pinned revision, chosen
# by measuring on YAAP's own data. The files are checked against their SHA-256, so YAAP never downloads or runs a
# model it did not pin.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS embedding-model
ARG BGE_M3_REVISION=5617a9f61b028005a4858fdac845db406aefb181
WORKDIR /model
RUN python -c "import urllib.request as r; [r.urlretrieve(f'https://huggingface.co/BAAI/bge-m3/resolve/${BGE_M3_REVISION}/onnx/{name}', name) for name in ('model.onnx', 'model.onnx_data', 'Constant_7_attr__value', 'tokenizer.json')]" \
    && echo "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b  model.onnx" > SHA256SUMS \
    && echo "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4  model.onnx_data" >> SHA256SUMS \
    && echo "cdf16f72c5d07b36484056e601ed9687f78477e5d85cee85a34f2406b7fb5906  Constant_7_attr__value" >> SHA256SUMS \
    && echo "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790  tokenizer.json" >> SHA256SUMS \
    && sha256sum -c SHA256SUMS

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YAAP_DATA_DIR=/data \
    YAAP_STATIC_DIR=/app/static \
    YAAP_PREVIEW_BIND=0.0.0.0 \
    CLAUDE_CONFIG_DIR=/data/claude-code \
    CODEX_HOME=/data/codex \
    DISABLE_AUTOUPDATER=1

# Claude Code from Anthropic's signed apt repository (stable channel), unmodified. YAAP runs it for the
# "Claude Code" connection; the sign-in is stored by Claude Code itself under CLAUDE_CONFIG_DIR.
# The build stops when the signing key does not have Anthropic's published fingerprint.
ARG CLAUDE_CODE_KEY_FINGERPRINT=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg git \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o /etc/apt/keyrings/claude-code.asc \
    && gpg --show-keys --with-colons /etc/apt/keyrings/claude-code.asc | grep -q "^fpr:::::::::${CLAUDE_CODE_KEY_FINGERPRINT}:" \
    && echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
        > /etc/apt/sources.list.d/claude-code.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends claude-code \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && claude --version

# Node.js and npm for Codex below, taken from the same image as the frontend build. The agent's commands, MCP servers
# over stdio and its browser run in the runner of their environment (docker/sandbox), not in this image.
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# Codex, OpenAI's CLI, unmodified, for the "Codex" connection; the sign-in is stored by Codex itself under CODEX_HOME.
# The version is pinned: YAAP switches off Codex's own tools by feature name, and Codex refuses names it does not know.
ARG CODEX_VERSION=0.166.0
RUN npm install --global --no-fund --no-audit "@openai/codex@${CODEX_VERSION}" \
    && codex --version

# OCR for scanned PDFs and images added as knowledge or attachments: Tesseract, Dutch and English.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --version

COPY --from=embedding-model /model/model.onnx /model/model.onnx_data /model/Constant_7_attr__value /model/tokenizer.json /opt/models/bge-m3/

RUN useradd --create-home --uid 1000 yaap \
    && mkdir -p /data \
    && chown yaap:yaap /data
WORKDIR /app
# Every package, also the indirect ones, at the version and with the hashes of the lock file (CONTRIBUTING.md).
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock
COPY backend/app ./app
COPY --from=frontend /build/dist ./static
USER yaap
# 8081: the preview of a project's app (YAAP_PREVIEW_PORT), a second listener the app starts itself.
EXPOSE 8080 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
