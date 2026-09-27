"""Stable double-submit CSRF token helper for admin pages."""
from __future__ import annotations

import re
import secrets

from fastapi import Request


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def get_or_create_csrf_token(request: Request) -> str:
    token = request.cookies.get("csrf_token", "")
    if _TOKEN_PATTERN.fullmatch(token):
        return token
    return secrets.token_urlsafe(32)
