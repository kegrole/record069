"""Content fingerprinting helpers."""

from __future__ import annotations

import hashlib


def content_fingerprint(body: bytes | str) -> str:
    """Return the lowercase hex sha256 digest of a response body.

    Text bodies are encoded as UTF-8 before hashing so that fixture strings
    and raw network bytes produce identical fingerprints.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()
