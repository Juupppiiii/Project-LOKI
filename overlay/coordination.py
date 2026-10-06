"""Database-backed coordination primitives shared by repository backends."""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    name: str
    owner_id: str
    fencing_token: int
    expires_at: str


def new_lease_owner(prefix: str = "loki") -> str:
    """Unique process-instance identity; never reuse hostname or PID alone."""

    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
