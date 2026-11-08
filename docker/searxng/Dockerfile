# Build context: the repository root (see docker-compose.yml).
# SearXNG, pinned and unmodified, with YAAP's settings as the template its entrypoint starts from.
FROM searxng/searxng:2026.11.5-3e95f6cd2@sha256:ffad724acd87b8dd312328d18da88ddd42e5c4b1d23231594662620053c13cae
COPY docker/searxng/settings.yml /usr/local/searxng/settings.template.yml
