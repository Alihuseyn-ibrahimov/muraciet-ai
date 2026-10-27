"""Yeni sistemin API-si (spec 4-cü bölmə): botlar, veb-vidcet və n8n üçün daxili endpoint-lər.

İşə salmaq:  uvicorn app.api:app
Köhnə prototipin API-si (app/main.py) Faza 9-da keçiddən sonra silinir.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import admin, bot, internal, web

WIDGET = Path(__file__).resolve().parents[2] / "web" / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(title="Vətəndaş müraciətləri API", version="2.0")
    app.include_router(bot.router)
    app.include_router(web.router)
    app.include_router(internal.router)
    app.include_router(admin.router)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def widget() -> FileResponse:
        """Veb-vidcet (yalnız ümumi suallar)."""
        return FileResponse(WIDGET, media_type="text/html; charset=utf-8")

    return app


app = create_app()
