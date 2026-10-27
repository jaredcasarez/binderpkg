from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class BinderProject(Base):
    __tablename__ = "binder_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="Untitled Binder")
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Page(BaseModel):
    label: str = ""
    slots: list[str] = Field(default_factory=list)


class BinderDocument(BaseModel):
    cols: int = Field(default=3, ge=1, le=5)
    rows: int = Field(default=3, ge=1, le=5)
    pages: list[Page] = Field(default_factory=lambda: [Page(label="Page 1")])
    custom_groups: dict[str, Any] = Field(default_factory=dict)
    card_history: list[str] = Field(default_factory=list)
    sleeved: bool = False
    sleeve_color: str | None = None
    slot_color: str | None = None
    sel_color: str | None = None
    border_color: str | None = None
    page_color: str | None = None

    @field_validator("pages")
    @classmethod
    def require_page(cls, pages: list[Page]) -> list[Page]:
        if not pages:
            raise ValueError("A binder must contain at least one page")
        return pages


class BinderWrite(BaseModel):
    name: str = Field(default="Untitled Binder", min_length=1, max_length=255)
    document: BinderDocument


class BinderRead(BinderWrite):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./binder-studio.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session


def get_project(project_id: int, db: Session) -> BinderProject:
    project = db.get(BinderProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Binder project not found")
    return project


def serialize(project: BinderProject) -> BinderRead:
    return BinderRead(
        id=project.id,
        name=project.name,
        document=BinderDocument.model_validate(project.document),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def binderpkg_services():
    source_path = os.getenv("BINDERPKG_PATH")
    if source_path and source_path not in sys.path:
        sys.path.insert(0, source_path)
    try:
        from data import services
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Card service is unavailable; set BINDERPKG_PATH to the binderpkg checkout",
        ) from exc
    return services


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Binder Studio API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/binders", response_model=list[BinderRead])
def list_binders(db: Session = Depends(get_db)) -> list[BinderRead]:
    projects = db.scalars(select(BinderProject).order_by(BinderProject.updated_at.desc()))
    return [serialize(project) for project in projects]


@app.post("/api/v1/binders", response_model=BinderRead, status_code=status.HTTP_201_CREATED)
def create_binder(payload: BinderWrite, db: Session = Depends(get_db)) -> BinderRead:
    project = BinderProject(name=payload.name, document=payload.document.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return serialize(project)


@app.get("/api/v1/binders/{project_id}", response_model=BinderRead)
def read_binder(project_id: int, db: Session = Depends(get_db)) -> BinderRead:
    return serialize(get_project(project_id, db))


@app.put("/api/v1/binders/{project_id}", response_model=BinderRead)
def update_binder(
    project_id: int, payload: BinderWrite, db: Session = Depends(get_db)
) -> BinderRead:
    project = get_project(project_id, db)
    project.name = payload.name
    project.document = payload.document.model_dump()
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return serialize(project)


@app.delete("/api/v1/binders/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_binder(project_id: int, db: Session = Depends(get_db)) -> Response:
    db.delete(get_project(project_id, db))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/cards/search")
def search_cards(
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=24, ge=1, le=100),
) -> dict[str, Any]:
    urls = binderpkg_services().search_limitless_tcg(q)
    return {"items": urls[:limit], "total": len(urls)}


@app.get("/api/v1/cards/resolve")
async def resolve_card(url: str = Query(min_length=1)) -> dict[str, Any]:
    result = await binderpkg_services().fetch_card_info_async(url)
    if error := result.get("error"):
        raise HTTPException(status_code=422, detail=error)
    return result
