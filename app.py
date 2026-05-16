import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import init_pool, init_schema, close_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ["DATABASE_URL"]
    pool = await init_pool(database_url)
    await init_schema(pool)
    from auth import bootstrap_passwords
    await bootstrap_passwords(pool)
    app.state.pool = pool
    try:
        yield
    finally:
        await close_pool(pool)


app = FastAPI(title="CTSS Carnival Token System", lifespan=lifespan)

from routes.auth_routes import router as auth_router
app.include_router(auth_router)

from routes.admin import router as admin_router
app.include_router(admin_router)

from routes.booth import router as booth_router
app.include_router(booth_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
