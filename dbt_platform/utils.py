"""Shared utilities used across the platform."""
import uuid


def gen_uuid() -> str:
    return str(uuid.uuid4())
