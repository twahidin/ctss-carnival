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

from routes.teacher import router as teacher_router
app.include_router(teacher_router)

from routes.summary import router as summary_router
app.include_router(summary_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/admin")


def _make_page_handler(file: str):
    async def serve() -> FileResponse:
        return FileResponse(f"static/{file}")
    return serve


for _path, _file in (
    ("/admin", "admin.html"),
    ("/booth", "booth.html"),
    ("/teacher", "teacher.html"),
    ("/summary", "summary.html"),
    ("/booth-slides", "booth-slides.html"),
    ("/sec2-teacher", "sec2-teacher-slide.html"),
):
    app.get(_path, include_in_schema=False)(_make_page_handler(_file))
