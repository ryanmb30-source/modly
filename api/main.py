"""
Modly FastAPI backend.
Runs locally within the Electron app to provide AI inference endpoints.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi import HTTPException

from routers import generation, model, optimize, status, settings, extensions, export, workflow_runs, agent
from services.safe_paths import resolve_within


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize the registry (instantiates all adapters)
    from services.generator_registry import generator_registry
    generator_registry.initialize()
    yield
    # Shutdown: unload all models
    generator_registry.unload_all()


class _StatusFilter(logging.Filter):
    def filter(self, record):
        return "/generate/status/" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_StatusFilter())


app = FastAPI(
    title="Modly API",
    version="0.4.1",
    lifespan=lifespan,
)

# The only legitimate caller is the Electron renderer. In production it is loaded
# with loadFile(), so its origin is file:// and the browser sends "null"; in dev it
# is ELECTRON_RENDERER_URL on a localhost port.
#
# This replaces allow_origins=["*"], which let any web page the user happened to
# visit call every unauthenticated endpoint on this API and read the responses.
#
# CAVEAT: "null" is not a strong identity -- a sandboxed iframe on a hostile page
# also sends Origin: null. Origin checks alone cannot fully authenticate the
# renderer; a shared token minted by Electron at spawn time is the real fix.
# Tracked separately.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
    # drei's SplatLoader reads Content-Length to size its buffers; cross-origin
    # JS can only see it when the server explicitly exposes the header.
    expose_headers=["Content-Length"],
)

app.include_router(status.router)
app.include_router(settings.router)
app.include_router(model.router,      prefix="/model")
app.include_router(generation.router, prefix="/generate")
app.include_router(optimize.router,    prefix="/optimize")
app.include_router(extensions.router, prefix="/extensions")
app.include_router(export.router,          prefix="/export")
app.include_router(workflow_runs.router,   prefix="/workflow-runs")
app.include_router(agent.router)

# Serve generated files from workspace — dynamic so path changes take effect immediately
@app.get("/workspace/{full_path:path}")
async def serve_workspace_file(full_path: str):
    import services.generator_registry as reg
    file_path = resolve_within(reg.WORKSPACE_DIR, full_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))
