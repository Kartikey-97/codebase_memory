from contextlib import asynccontextmanager

# --- PYDANTIC V1 MONKEY PATCH FOR TYPEALIASTYPE BUG ---
try:
    import pydantic.v1.fields
    import pydantic.v1.validators
    original_find_validators = pydantic.v1.validators.find_validators
    def safe_find_validators(type_, config):
        if type(type_).__name__ == "TypeAliasType":
            return []
        return original_find_validators(type_, config)
    pydantic.v1.validators.find_validators = safe_find_validators
    pydantic.v1.fields.find_validators = safe_find_validators
except ImportError:
    pass
# --------------------------------------------------------

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, ingest, insights, sync, graph
from app.config import get_settings
from app.db.mongo import close_client, setup_indexes


@asynccontextmanager
async def lifespan(_: FastAPI):
    await setup_indexes()
    yield
    await close_client()


settings = get_settings()
app = FastAPI(title="Codebase Memory API", version="0.1.0", lifespan=lifespan)

origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(ingest.router)
app.include_router(insights.router)
app.include_router(chat.router)
app.include_router(sync.router)
app.include_router(graph.router)
