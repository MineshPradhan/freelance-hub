"""Shared rate limiter (imported by main.py and the routers).

Uses in-memory storage by default. For multi-worker deployments point it at Redis:
    Limiter(..., storage_uri="redis://localhost:6379")
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
