"""Stable ARM deployment names, independent of public resource identities."""
from __future__ import annotations

import hashlib

DEPLOYMENT_NAME_LIMIT = 64


def deployment_name(name: str) -> str:
    """Preserve existing valid lengths; shorten only internal deployment names."""
    if len(name) <= DEPLOYMENT_NAME_LIMIT:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"{name[:DEPLOYMENT_NAME_LIMIT - len(digest) - 1]}-{digest}"
