from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.routes import aihybrid
from app.api.v1.router import api_router
from app.core.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Case Flow API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(aihybrid.router)
    # 诊断修复关键截图：落盘目录通过 /media 静态暴露（后续可挂数据卷）。
    image_dir = Path(settings.repair_image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(image_dir)), name="media")
    return app


app = create_app()
