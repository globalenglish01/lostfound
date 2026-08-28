"""Lost & Found Intelligent Matching Platform — FastAPI 入口。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import admin, items, matches, search
from .config import settings

app = FastAPI(
    title="Lost & Found Intelligent Matching Platform",
    version="1.0.0",
    description=(
        "Embedding 负责『找得出来』，结构化属性负责『比得准』，"
        "Hard Constraint 负责『排错』，Ranking 负责『排顺序』，人工确认负责『最终归属』。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(items.router)
app.include_router(search.router)
app.include_router(matches.router)
app.include_router(admin.router)

_STATIC = Path(__file__).parent / "static"


@app.get("/health")
def health():
    return {
        "status": "ok",
        "algorithm_version": settings.algorithm_version,
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
    }


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")
