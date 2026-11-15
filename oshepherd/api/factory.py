"""Select a gateway without initializing the other backend."""


def create_api_app(config, *, backend="redis"):
    if backend == "redis":
        from oshepherd.api.app import setup_api_app

        return setup_api_app(config)
    if backend == "supabase":
        from oshepherd.backends.supabase.gateway import create_supabase_app

        return create_supabase_app(config)
    raise ValueError("unsupported_backend")
