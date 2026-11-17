from fastapi import FastAPI

# Minimal Vercel FastAPI entrypoint — re-exports backend app when possible.
# Falls back to a lightweight stub endpoint if importing the heavy backend fails.

app = FastAPI()

try:
    # Attempt to import the real backend FastAPI instance
    from backend.main import app as backend_app

    # If the backend defines routers, include them into this app
    try:
        for r in getattr(backend_app, "router").routes:
            app.router.routes.append(r)
    except Exception:
        # If direct router copy fails, try include_router if available
        try:
            app.include_router(backend_app.router)
        except Exception:
            pass

except Exception as _import_error:
    # Expose a lightweight health endpoint so Vercel can start the server.
    @app.get("/__backend_import_error")
    def backend_import_error():
        return {"error": "backend import failed", "detail": str(_import_error)}
