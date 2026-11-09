FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server python3 iproute2 ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --create-home --shell /bin/sh --password '*' fleet && mkdir -p /run/sshd /etc/ssh/authorized_keys
COPY --chown=root:root public_key.pub /etc/ssh/authorized_keys/fleet
COPY sshd_config /etc/ssh/sshd_config
EXPOSE 2222
CMD ["/bin/sh", "-c", "mkdir -p /run/sshd && exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config"]
