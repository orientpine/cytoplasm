FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        systemd systemd-sysv libpam-systemd dbus python3 git curl ca-certificates \
        openssh-client sudo passwd tar \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV container=docker
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
