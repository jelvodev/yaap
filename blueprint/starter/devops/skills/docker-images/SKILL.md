---
name: docker-images
description: Write a Dockerfile and .dockerignore for a small, pinned, non-root image with a multi-stage build, a
  health check and no secrets in its layers. Use it whenever you create or change a Dockerfile, Containerfile or
  compose file, containerise an app, or shrink or review an image.
license: MIT
metadata:
  sources: own
---

# Docker images

A good image is small, rebuilds the same way every time, runs as an ordinary user and holds nothing secret. Read the
application first: its language and version, how it is built, the port it listens on and whether it has a health
endpoint.

## You write the files, the user builds

The agent in YAAP cannot run Docker in its runner. Write the Dockerfile, the `.dockerignore` and any compose file,
check what can be checked without Docker (a Dockerfile linter when the project has one, the build steps run directly
with `run_command`), and end with the exact commands for the user to build and run the image. Never claim an image
builds or starts when you could not run it.

For a larger task, when `delegate` is available (in a project conversation), delegate to the `devops` agent
(`agent: devops`) and review its diff.

## 1. Choose and pin the base

- Use the smallest base that runs the application: a `-slim` image of the language, or a distroless one when nothing
  needs a shell. Larger bases carry more packages to patch.
- Match the language version the project uses (`.python-version`, `engines`, `go.mod`).
- Pin it by digest, with the tag kept for readers. The user gets the digest with
  `docker buildx imagetools inspect python:3.13-slim`; an update bot can keep it current.

## 2. Build in stages

The build stage holds compilers, package managers and caches; the final stage holds only what runs. Copy the lock
file and install dependencies before copying the source, so a code change does not reinstall every dependency.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:<digest> AS build
COPY --from=ghcr.io/astral-sh/uv:<version>@sha256:<digest> /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:3.13-slim@sha256:<digest>
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build --chown=10001:10001 /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Both stages use the same base, so the virtual environment's interpreter path exists in the final image. For other
languages the shape is the same: dependencies first, then the source, then copy only the build output.

## 3. Run as an ordinary user

Create a user with a fixed numeric id and switch to it with `USER`. A numeric id lets Kubernetes verify
`runAsNonRoot`. Files the application must write go to a volume or `/tmp`, not into the image.

## 4. Keep the build context small

A `.dockerignore` keeps secrets and clutter out of the context, which also makes builds faster:

```
.git
.env
.env.*
!.env.example
.venv
node_modules
__pycache__
*.pyc
dist
coverage
.idea
.vscode
```

## 5. No secrets in layers

Every `COPY`, `ARG` and `ENV` value is stored in the image and its history, readable by anyone who pulls it. A token
needed during the build (for a private package index) comes in as a build secret, which is not stored:

```dockerfile
RUN --mount=type=secret,id=netrc,target=/root/.netrc uv sync --locked --no-dev
```

The user passes it with `docker build --secret id=netrc,src=$HOME/.netrc .`. Runtime secrets come from the
environment or a mounted file when the container starts, never from the Dockerfile.

## 6. Health and signals

- A `HEALTHCHECK` lets Docker and compose see that the application answers, not only that the process exists. Use a
  tool that is in the image; slim and distroless images often lack `curl`. Kubernetes ignores `HEALTHCHECK` and uses
  probes instead (see the `kubernetes-manifests` skill).
- Write `CMD` and `ENTRYPOINT` in exec form (a JSON list), so the application is process 1 and receives the stop
  signal; the shell form wraps it in a shell that does not pass signals on.
- One process per container. Run a second one in its own container.

## 7. Labels

Add `LABEL org.opencontainers.image.source="<repository url>"` so a registry can link the image to its code.

## Checklist

- [ ] Base pinned by digest, smallest that works, version matching the project
- [ ] Multi-stage, dependencies installed before the source is copied
- [ ] Numeric non-root `USER`
- [ ] `.dockerignore` excludes `.git`, `.env*` and local environments
- [ ] No secret in `COPY`, `ARG`, `ENV` or a layer; build secrets by mount
- [ ] `HEALTHCHECK` with a tool present in the image; exec-form `CMD`

## Commands for the user

End with the commands the user runs, adjusted to the project:

```
docker build -t <name>:dev .
docker run --rm -p 8000:8000 <name>:dev
docker ps --format "{{.Names}} {{.Status}}"
docker image ls <name>:dev
```

The status column shows `(healthy)` once the health check passes; the image list shows the size. Ask the user for
the output when the result matters to what comes next.
