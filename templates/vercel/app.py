"""Vercel imports this ASGI application; inference runs on enrolled workers."""

from oshepherd.api.factory import create_vercel_app

app = create_vercel_app()
