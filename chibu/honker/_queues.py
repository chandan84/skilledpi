"""Named work queue accessors."""

from __future__ import annotations

import honker

from ._db import get_hdb


def snapshot_queue() -> honker.Queue:
    return get_hdb().queue("snapshot", max_attempts=5, visibility_timeout_s=30)


def reload_queue() -> honker.Queue:
    return get_hdb().queue("hot_reload", max_attempts=3, visibility_timeout_s=20)
