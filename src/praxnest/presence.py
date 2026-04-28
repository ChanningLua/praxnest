"""In-memory presence store — track which users are "online" via heartbeat.

Why in-memory not SQLite: presence churns on every request (heartbeat
every ~20s per active user). Hitting SQLite that hard on every page
view is wasteful, and we don't need durability — losing presence on
restart just means everyone shows "offline" for 20 seconds until
their next heartbeat lands. Cheap to lose, expensive to persist.

Thread-safety: FastAPI may run handlers on different async workers,
but they share Python process memory. Dict mutation is safe under
GIL for our access pattern (single-key writes) so we don't need a
lock here.
"""

from __future__ import annotations

import time
from typing import Any


# user_id → last_seen_unix_timestamp
_LAST_SEEN: dict[int, float] = {}

# A user is "online" if their last heartbeat was within this many seconds.
# 90s gives some slack — clients heartbeat every 30s, so we tolerate one
# missed beat before showing offline.
ONLINE_WINDOW_SECONDS = 90


def heartbeat(user_id: int) -> None:
    """Record that user_id is alive right now."""
    _LAST_SEEN[user_id] = time.time()


def is_online(user_id: int, *, now: float | None = None) -> bool:
    last = _LAST_SEEN.get(user_id)
    if last is None:
        return False
    return (now or time.time()) - last <= ONLINE_WINDOW_SECONDS


def online_user_ids(*, now: float | None = None) -> list[int]:
    """Return ids of users seen in the last ONLINE_WINDOW_SECONDS."""
    now = now or time.time()
    threshold = now - ONLINE_WINDOW_SECONDS
    return [uid for uid, last in _LAST_SEEN.items() if last >= threshold]


def reset_for_tests() -> None:
    """Wipe the store. Tests call this in setUp/tearDown to avoid
    cross-test bleed."""
    _LAST_SEEN.clear()
