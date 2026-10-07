"""Select a gateway without initializing the other backend."""


def create_api_app(config, *, backend="redis"):
    if backend == "redis":
        from oshepherd.api.app import setup_api_app

        return setup_api_app(config)
    if backend == "supabase":
        from oshepherd.backends.supabase.gateway import create_supabase_app

        return create_supabase_app(config)
    raise ValueError("unsupported_backend")


def create_vercel_app():
    """Resource-free ASGI export; no Uvicorn, signals, migrations or collectors."""
    import os
    from oshepherd.backends.supabase.config import SupabaseGatewayConfig

    if os.environ.get("OSHEPHERD_BACKEND") != "supabase":
        raise ValueError("Vercel requires OSHEPHERD_BACKEND=supabase")
    if os.environ.get("OSHEPHERD_GATEWAY_PROFILE") != "vercel":
        raise ValueError("Vercel requires the vercel gateway profile")
    if (
        os.environ.get("VERCEL_ENV") == "preview"
        and os.environ.get("OSHEPHERD_ALLOW_PREVIEW_INFERENCE") != "true"
    ):
        raise ValueError(
            "Preview inference is disabled; explicit isolated configuration is required"
        )
    # Deliberately do not load a checkout .env file into a hosted import.
    values = {
        name: os.environ[name]
        for name in SupabaseGatewayConfig.model_fields
        if name in os.environ
    }
    try:
        config = SupabaseGatewayConfig(**values)
    except ValueError:
        raise ValueError("Invalid server-only Vercel gateway configuration") from None
    return create_api_app(config, backend="supabase")
