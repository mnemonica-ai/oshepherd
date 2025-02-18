def load_health_routes(app):

    @app.get("/health")
    async def health():
        return {"status": 200}

    return app
