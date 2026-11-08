#!/bin/sh
# Start a YAAP sandbox: install the host key and the authorized key the helper passed in, then run sshd in the
# foreground. Both come from YAAP: it made the host key, so it pins it without asking.
set -eu

if [ -z "${YAAP_HOST_KEY:-}" ] || [ -z "${YAAP_AUTHORIZED_KEY:-}" ]; then
    echo "yaap-sandbox: YAAP_HOST_KEY and YAAP_AUTHORIZED_KEY must be set; the yaap-sandbox helper sets them." >&2
    exit 1
fi

umask 077
printf '%s' "$YAAP_HOST_KEY" | base64 -d > /etc/ssh/ssh_host_ed25519_key
ssh-keygen -y -f /etc/ssh/ssh_host_ed25519_key > /etc/ssh/ssh_host_ed25519_key.pub
chmod 0644 /etc/ssh/ssh_host_ed25519_key.pub

install -d -m 0700 -o yaap -g yaap /home/yaap/.ssh
printf '%s\n' "$YAAP_AUTHORIZED_KEY" > /home/yaap/.ssh/authorized_keys
chown yaap:yaap /home/yaap/.ssh/authorized_keys
chmod 0600 /home/yaap/.ssh/authorized_keys

mkdir -p /run/sshd
# sshd starts with an empty environment, so the keys are not passed on to the agent's sessions.
exec env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /usr/sbin/sshd -D -e
