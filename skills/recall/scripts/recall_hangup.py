"""Stop recall when the caller hangs up (owner Q433).

YooDori's node-read runs recall over a non-pty SSH session. When she gives up on a slow call, sshd
closes the pipe behind our stdout but sends no signal, so the search and its model relay call kept
running and held a relay slot. A daemon thread watches stdout for the hang-up and exits at once;
the relay ssh child dies with us through its parent-death signal (omo_relay.client).
"""
from __future__ import annotations

import os
import select
import sys
import threading
from typing import Final

CANCELLED: Final = 130
_POLL_MS: Final = 500


def _watch(fd: int) -> None:
    watch = select.poll()
    watch.register(fd, 0)  # mask 0: only POLLHUP/POLLERR, i.e. the reading side is gone
    while True:
        if any(event & (select.POLLHUP | select.POLLERR) for _fd, event in watch.poll(_POLL_MS)):
            os._exit(CANCELLED)


def exit_on_hangup() -> None:
    try:
        fd = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return
    threading.Thread(target=_watch, args=(fd,), name="recall-hangup", daemon=True).start()
