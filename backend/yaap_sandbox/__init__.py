"""yaap-sandbox: the small helper service that starts YAAP's sandbox containers.

It is the only part of YAAP that talks to Docker, and it does so through a docker-socket-proxy limited to the
container and image sections of the API. YAAP itself never gets the Docker socket. The helper:

- creates, starts, stops, inspects and removes containers that carry YAAP's sandbox label, and refuses any other
  container, also one that happens to have a sandbox's name;
- creates a sandbox only from the sandbox image, which carries its own label;
- relays YAAP's SSH connection to a sandbox's sshd (port 2222), so YAAP never shares a network with a sandbox;
- reads where YAAP's own /data is on the host, so YAAP can tell the agent the user's path of a project folder.

YAAP authenticates with a token the helper writes into a volume that only the two of them mount. The package has no
dependency on YAAP's backend (`app`), so its image holds only this folder, FastAPI, uvicorn and httpx.
"""
