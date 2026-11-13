# The yaap-sandbox helper: the only part of YAAP that talks to Docker, through the docker-socket-proxy.
# Build context: the repository root (see docker-compose.build.yml). It holds only backend/yaap_sandbox and three
# packages with what they need, installed from the lock file next to this one (requirements.txt lists the three).
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# zlib1g is named so the fixed Debian security build is installed even when the base image is older than that build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends zlib1g \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY docker/sandbox-helper/requirements.lock ./
# pip is not needed at runtime; removing it also drops its vendored copies of urllib3 and others.
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock \
    && pip uninstall --yes pip
COPY backend/yaap_sandbox ./yaap_sandbox
# It runs as root inside its own container only to write the token into the volume it shares with YAAP; what it may
# do with Docker is limited by the proxy and by its own label checks.
EXPOSE 8000 2222
CMD ["python", "-m", "yaap_sandbox"]
