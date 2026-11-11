#!/bin/sh
# Generated for one Linux device. Keep this short-lived enrollment credential private.
set -eu
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
[ "$(uname -s)" = Linux ] || { echo 'This connection kit is for Linux.'; exit 1; }
[ "$(id -u)" != 0 ] || { echo 'Run as the named target account: sh ./Connect.sh. Do not run the whole script as root.'; exit 1; }
RF_USER=$(id -un)
case "$RF_USER" in *[!a-zA-Z0-9_.-]*|'') echo 'Unsupported account name'; exit 1;; esac
RF_TMP=$(mktemp -d /tmp/remote-fleet.XXXXXXXX)
trap 'rm -rf "$RF_TMP"' EXIT HUP INT TERM
@@METADATA@@
printf '%s\n' 'Remote Fleet: connect this Linux device to the approved engineer.' 'No Python is required for pairing. Administrator approval may be requested for SSH setup.'
RF_MISSING=''
command -v curl >/dev/null 2>&1 || RF_MISSING="$RF_MISSING curl"
command -v sshd >/dev/null 2>&1 || RF_MISSING="$RF_MISSING openssh-server"
if [ -n "$RF_MISSING" ]; then
    command -v sudo >/dev/null 2>&1 || { echo 'Administrator setup is required: install curl and OpenSSH Server, then run this kit again.'; exit 1; }
    sudo -v
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-upgrade $RF_MISSING
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y $RF_MISSING
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper --non-interactive install curl openssh
    elif command -v apk >/dev/null 2>&1; then
        sudo apk add --no-cache $RF_MISSING
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm curl openssh
    else
        echo 'Install curl and OpenSSH Server with your distribution tools, then run the kit again.'; exit 1
    fi
fi
[ ! -L "$HOME/.ssh" ] || { echo 'SSH directory is a symbolic link. Setup stopped.'; exit 1; }
mkdir -p "$HOME/.ssh"
[ "$(stat -c %u "$HOME/.ssh")" = "$(id -u)" ] || exit 1
if command -v setfacl >/dev/null 2>&1; then setfacl -b -k "$HOME/.ssh"; fi
chmod 700 "$HOME/.ssh"
RF_KEYS="$HOME/.ssh/authorized_keys"
[ ! -L "$RF_KEYS" ] || { echo 'SSH key file is a symbolic link. Setup stopped.'; exit 1; }
if [ -e "$RF_KEYS" ]; then
    [ -f "$RF_KEYS" ] && [ "$(stat -c %u "$RF_KEYS")" = "$(id -u)" ] && [ "$(stat -c %h "$RF_KEYS")" = 1 ] || exit 1
fi
touch "$RF_KEYS"
if command -v setfacl >/dev/null 2>&1; then setfacl -b "$RF_KEYS"; fi
chmod 600 "$RF_KEYS"
if ! grep -Fqx "$RF_KEY_LINE" "$RF_KEYS"; then printf '\n%s\n' "$RF_KEY_LINE" >> "$RF_KEYS"; fi
# Generate missing host keys before starting a previously disabled daemon.
if [ ! -f /etc/ssh/ssh_host_ed25519_key.pub ]; then sudo ssh-keygen -A; fi
# Existing daemon configuration, account restrictions and firewall policy are preserved.
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    if ! systemctl is-active --quiet ssh && ! systemctl is-active --quiet sshd; then
        sudo -v
        if systemctl cat ssh.service >/dev/null 2>&1; then sudo systemctl start ssh; else sudo systemctl start sshd; fi
    fi
elif command -v rc-service >/dev/null 2>&1; then
    rc-service sshd status >/dev/null 2>&1 || sudo rc-service sshd start
elif command -v service >/dev/null 2>&1; then
    service ssh status >/dev/null 2>&1 || service sshd status >/dev/null 2>&1 || { sudo service ssh start || sudo service sshd start; }
else
    echo 'No supported service manager found. Existing SSH must already be listening on port 22.'
fi
# These fields are restricted to safe ASCII before writing JSON; no target-side JSON runtime.
RF_HOST_KEY=$(awk 'NR==1 {print $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub)
case "$RF_HOST_KEY" in 'ssh-ed25519 '*) ;; *) echo 'No Ed25519 SSH host key available'; exit 1;; esac
RF_KEY_DATA=${RF_HOST_KEY#ssh-ed25519 }
case "$RF_KEY_DATA" in *[!a-zA-Z0-9+/=]*|'') echo 'Invalid SSH host key'; exit 1;; esac
printf '{"kit":"%s","token":"%s","username":"%s","host_key":"%s"}\n' "$RF_KIT" "$RF_TOKEN" "$RF_USER" "$RF_HOST_KEY" > "$RF_TMP/receipt.json"
curl --silent --show-error --fail --noproxy '*' --connect-timeout 10 --max-time 30 --cacert "$RF_TMP/controller.pem" -H 'Content-Type: application/json' --data-binary "@$RF_TMP/receipt.json" "$RF_URL" > "$RF_TMP/response.json"
printf '%s\n' 'Pairing received. The engineer workspace will verify SSH and show connection and Python progress separately.' 'Delete this kit after use. Run Remove.sh as this account to remove the kit’s SSH key.'
