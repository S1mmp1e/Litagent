"""SQLite transactions shared by HTTP handlers and the single background worker."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .settings import DB_FILE, prepare_dirs


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def connect():
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def migrate():
    prepare_dirs()
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL);
        CREATE TABLE IF NOT EXISTS profiles(
          id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(
          id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          file_hash TEXT NOT NULL, source TEXT NOT NULL, parser TEXT NOT NULL, index_version TEXT NOT NULL,
          status TEXT NOT NULL, page_count INTEGER DEFAULT 0, created_at REAL NOT NULL,
          UNIQUE(paper_id,file_hash,parser,index_version));
        CREATE TABLE IF NOT EXISTS pages(
          document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
          page INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(document_id,page));
        CREATE TABLE IF NOT EXISTS chunks(
          id TEXT PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          paper_id INTEGER NOT NULL, page INTEGER NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
          chunk_id UNINDEXED, text, tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
          result TEXT, error TEXT, stage TEXT DEFAULT 'queued', checkpoint TEXT DEFAULT '{}',
          session_id INTEGER, attempts INTEGER DEFAULT 0, cancel_requested INTEGER DEFAULT 0,
          created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS tasks_queue ON tasks(status,created_at);
        CREATE TABLE IF NOT EXISTS task_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
          event TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS notes(
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
          version INTEGER NOT NULL, body TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '',
          approved INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
          UNIQUE(task_id,version));
        CREATE TABLE IF NOT EXISTS reading_cache(
          cache_key TEXT PRIMARY KEY,
          document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          body TEXT NOT NULL, created_at REAL NOT NULL);
        """)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_sessions'").fetchone()
        if exists:
            columns = {r["name"] for r in db.execute("PRAGMA table_info(chat_sessions)")}
            for name, default in (("agent_messages", "[]"), ("context_signature", "")):
                if name not in columns:
                    db.execute(f"ALTER TABLE chat_sessions ADD COLUMN {name} TEXT DEFAULT '{default}'")
        db.execute("INSERT OR IGNORE INTO profiles VALUES(1,1,?)", (dumps({
            "background": "", "purpose": "理解论文方法与实验",
            "focus": "", "depth": "standard", "language": "中文",
        }),))
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(1,?)", (time.time(),))
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(2,?)", (time.time(),))
        from . import note_templates, notebook, personal
        note_templates.initialize(db)
        notebook.initialize(db)
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(3,?)", (time.time(),))
        personal.initialize(db)
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES(4,?)", (time.time(),))


def rows(sql, args=()):
    with connect() as db:
        return [dict(r) for r in db.execute(sql, args)]


def one(sql, args=()):
    result = rows(sql, args)
    return result[0] if result else None


def get_task(task_id):
    task = one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if task:
        for key in ("payload", "checkpoint", "result"):
            task[key] = json.loads(task[key]) if task[key] else None
    return task


def emit(task_id, event, data):
    with connect() as db:
        db.execute("INSERT INTO task_events(task_id,event,data,created_at) VALUES(?,?,?,?)",
                   (task_id, event, dumps(data), time.time()))


def checkpoint(task_id, stage, data):
    with connect() as db:
        db.execute("UPDATE tasks SET stage=?,checkpoint=?,updated_at=? WHERE id=?",
                   (stage, dumps(data), time.time(), task_id))
    emit(task_id, "stage", {"stage": stage})


def enqueue(kind, payload, session_id=None, resume_checkpoint=None):
    task_id = uuid.uuid4().hex
    now = time.time()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if session_id is not None and db.execute(
            "SELECT 1 FROM tasks WHERE session_id=? AND status IN ('queued','running')",
            (session_id,),
        ).fetchone():
            raise ValueError("该会话已有任务运行，请等待完成或取消。")
        if db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0] >= 30:
            raise ValueError("任务队列已满，请稍后重试。")
        db.execute("""INSERT INTO tasks(id,kind,status,payload,session_id,created_at,updated_at)
                      VALUES(?,?,'queued',?,?,?,?)""",
                   (task_id, kind, dumps(payload), session_id, now, now))
        if resume_checkpoint:
            db.execute("UPDATE tasks SET checkpoint=? WHERE id=?", (dumps(resume_checkpoint), task_id))
    emit(task_id, "stage", {"stage": "queued"})
    return get_task(task_id)
