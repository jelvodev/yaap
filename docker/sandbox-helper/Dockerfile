# The yaap-sandbox helper: the only part of YAAP that talks to Docker, through the docker-socket-proxy.
# Build context: the repository root (see docker-compose.yml). It holds only backend/yaap_sandbox and three packages
# with what they need, installed from the lock file next to this one (requirements.txt lists the three).
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY docker/sandbox-helper/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock
COPY backend/yaap_sandbox ./yaap_sandbox
# It runs as root inside its own container only to write the token into the volume it shares with YAAP; what it may
# do with Docker is limited by the proxy and by its own label checks.
EXPOSE 8000 2222
CMD ["python", "-m", "yaap_sandbox"]
