"""Named durable stream accessors."""

from __future__ import annotations

import honker

from ._db import get_hdb


def agent_events_stream() -> honker.Stream:
    return get_hdb().stream("agent_events")
