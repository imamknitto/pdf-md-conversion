from __future__ import annotations


class JobCancelled(Exception):
    """Raised inside a conversion worker when the job was cancelled."""
