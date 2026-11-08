# The yaap-sandbox helper: the only part of YAAP that talks to Docker, through the docker-socket-proxy.
# Build context: the repository root (see docker-compose.yml). It holds only backend/yaap_sandbox and three packages,
# pinned to the versions YAAP itself uses (backend/requirements.txt).
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir fastapi==0.162.0 uvicorn==0.54.0 httpx==0.28.1
WORKDIR /app
COPY backend/yaap_sandbox ./yaap_sandbox
# It runs as root inside its own container only to write the token into the volume it shares with YAAP; what it may
# do with Docker is limited by the proxy and by its own label checks.
EXPOSE 8000 2222
CMD ["python", "-m", "yaap_sandbox"]
