from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, Table, Text, create_engine,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from .settings import DB_FILE, prepare_dirs

Base = declarative_base()

paper_tags = Table(
    "paper_tags",
    Base.metadata,
    Column("paper_id", Integer, ForeignKey("papers.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True)
    title = Column(String(500), nullable=False)
    authors = Column(Text, default="[]")
    abstract = Column(Text, default="")
    year = Column(Integer)
    venue = Column(String(200))
    doi = Column(String(200))
    arxiv_id = Column(String(50), unique=True)
    pdf_url = Column(String(500))
    bibtex = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tags = relationship("Tag", secondary=paper_tags, back_populates="papers")
    paper_categories = relationship("PaperCategory", back_populates="paper", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)

    papers = relationship("Paper", secondary=paper_tags, back_populates="tags")


class Category(Base):
    """High-level classification folders (e.g., 人工智能, 无人对抗, 强化学习)."""
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, default="")
    color = Column(String(20), default="#6b7280")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    paper_categories = relationship("PaperCategory", back_populates="category", cascade="all, delete-orphan")


class PaperCategory(Base):
    """Junction table with approval status."""
    __tablename__ = "paper_categories"

    id = Column(Integer, primary_key=True)
    paper_id = Column(Integer, ForeignKey("papers.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    status = Column(String(20), default="pending")  # pending / approved / rejected
    suggested_by = Column(String(20), default="manual")  # ai / manual
    ai_reason = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    paper = relationship("Paper", back_populates="paper_categories")
    category = relationship("Category", back_populates="paper_categories")


class ChatSession(Base):
    """Persistent chat conversation history."""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), default="新对话")
    messages = Column(Text, default="[]")  # JSON: [{"role": "user/assistant", "content": "..."}]
    agent_messages = Column(Text, default="[]")
    context_signature = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


DB_PATH = f"sqlite:///{DB_FILE.as_posix()}"
engine = create_engine(
    DB_PATH,
    echo=False,
    connect_args={"check_same_thread": False},
)
# Enable WAL mode for concurrent read/write (prevents "database is locked" freezes)
from sqlalchemy import event
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

SessionLocal = sessionmaker(bind=engine)


def init_db():
    prepare_dirs()
    # Add columns before ORM queries; existing chat text is never reconstructed as tool history.
    from .store import migrate
    migrate()
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
