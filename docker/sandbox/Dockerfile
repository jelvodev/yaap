# The sandbox image: the runner of each environment and the sandbox of each project that
# chose one, started by the yaap-sandbox helper, reached by YAAP over SSH. Debian with common build tools, sshd and the
# agent's browser; the agent signs in as the user yaap (uid 1000, like YAAP itself, so files in the project folder keep
# one owner) and may use sudo inside the container.
# Build context: this folder (see docker-compose.yml).
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

# The helper creates sandboxes only from an image with this label.
LABEL org.yaap.sandbox.image="1"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server sudo ca-certificates curl wget git git-lfs \
        build-essential pkg-config make cmake \
        python3 python3-dev python3-venv python3-pip \
        nodejs npm \
        ripgrep jq less nano procps file unzip zip xz-utils bzip2 iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/bash yaap \
    && echo "yaap ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/yaap \
    && chmod 0440 /etc/sudoers.d/yaap \
    && mkdir -p /workspace /run/sshd \
    && chown yaap:yaap /workspace \
    && rm -f /etc/ssh/ssh_host_*

# The agent's browser: Microsoft's Playwright MCP server,
# pinned and unmodified, with the Chromium of the Playwright version it depends on and Chromium's system libraries.
# YAAP starts one headless browser per conversation in the runner through its MCP client. SSH sessions start with an
# empty environment, so the browsers' folder is also named in /etc/environment (YAAP passes it as well).
ARG PLAYWRIGHT_MCP_VERSION=0.0.83
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install --global --no-fund --no-audit "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
    && node "$(npm root --global)/@playwright/mcp/node_modules/playwright/cli.js" install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.npm \
    && chmod -R a+rX /opt/ms-playwright \
    && echo "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright" >> /etc/environment \
    && playwright-mcp --version

# Language servers: YAAP starts one per project and language where the project's commands run, over
# the same SSH connection as the shell sessions. Pinned; typescript-language-server needs TypeScript next to it.
# Servers for other languages (gopls, rust-analyzer, clangd) go into the extra layer below.
ARG PYRIGHT_VERSION=1.1.390
ARG TYPESCRIPT_LANGUAGE_SERVER_VERSION=4.3.3
ARG TYPESCRIPT_VERSION=5.6.3
RUN npm install --global --no-fund --no-audit \
        "pyright@${PYRIGHT_VERSION}" \
        "typescript-language-server@${TYPESCRIPT_LANGUAGE_SERVER_VERSION}" \
        "typescript@${TYPESCRIPT_VERSION}" \
    && rm -rf /root/.npm \
    && pyright --version \
    && typescript-language-server --version

COPY sshd.conf /etc/ssh/sshd_config.d/yaap.conf
COPY entrypoint.sh /usr/local/bin/yaap-sandbox-entrypoint
RUN chmod 0755 /usr/local/bin/yaap-sandbox-entrypoint

# Extra packages for the runners and sandboxes: one extra layer, named in docker-compose.override.yml as build args of
# the service sandbox-image (docs/configuration.md, "Extra packages for the runner"). Empty, it adds nothing.
ARG EXTRA_APT_PACKAGES=""
ARG EXTRA_SETUP=""
RUN if [ -n "$EXTRA_APT_PACKAGES" ]; then \
        apt-get update && apt-get install -y --no-install-recommends $EXTRA_APT_PACKAGES && rm -rf /var/lib/apt/lists/*; \
    fi \
    && if [ -n "$EXTRA_SETUP" ]; then sh -euc "$EXTRA_SETUP"; fi

WORKDIR /workspace
EXPOSE 22
ENTRYPOINT ["/usr/local/bin/yaap-sandbox-entrypoint"]
