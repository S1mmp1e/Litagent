"""Local diagnostics and SQLite/PDF backups. Never prints credentials."""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime
from contextlib import closing
from pathlib import Path

from lit_agent.settings import DATA, DB_FILE, MODEL_NAME, ROOT


def doctor(load_model=False):
    packages = {name: importlib.util.find_spec(name) is not None for name in (
        "fastapi", "uvicorn", "sqlalchemy", "pypdf", "qdrant_client",
        "sentence_transformers", "torch")}
    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LIT_API_KEY") or ""
    report = {"database": str(DB_FILE), "data": str(DATA), "packages": packages,
              "key_present": bool(key.strip()), "key_validated": False,
              "embedding_model": MODEL_NAME, "text_model": os.getenv("LIT_MODEL", "deepseek-chat"),
              "embedding": "not_loaded", "counts": {}}
    if DB_FILE.is_file():
        with closing(sqlite3.connect(DB_FILE.as_uri() + "?mode=ro", uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
            for name in ("papers", "categories", "chat_sessions", "documents"):
                if name in tables:
                    report["counts"][name] = db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    if load_model:
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", LIT_ALLOW_MODEL_DOWNLOAD="0")
        try:
            from lit_agent.retrieval import get_embedder
            result = get_embedder().encode(["query: diagnostic"], normalize_embeddings=True)
            report["embedding"] = "ready" if list(result.shape) == [1, 384] else "unexpected_shape"
        except Exception:
            report["embedding"] = "unavailable"
    return report


def backup(output_root=None):
    root = Path(output_root or ROOT / "backups").resolve()
    destination = root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"complete": False, "database": "lit_agent.db", "pdf_files": [],
                "vectors_included": False, "restore_note": "Stop the app before restoring. Rebuild vector indexes from PDFs."}
    manifest_path = destination / "manifest.json"
    try:
        with closing(sqlite3.connect(DB_FILE.as_uri() + "?mode=ro", uri=True)) as source:
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master")}
            if "tasks" in tables and source.execute(
                    "SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]:
                raise ValueError("请先等待任务完成并停止服务，再执行完整备份。")
            with closing(sqlite3.connect(destination / "lit_agent.db")) as target:
                source.backup(target)
        with closing(sqlite3.connect(destination / "lit_agent.db")) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
            digests = [r[0] for r in db.execute("SELECT DISTINCT file_hash FROM documents")] if "documents" in tables else []
            if "note_sources" in tables:
                digests = sorted(set(digests) | {r[0] for r in db.execute("SELECT DISTINCT file_hash FROM note_sources")})
            manifest["counts"] = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                  for table in ("papers", "categories", "chat_sessions") if table in tables}
        (destination / "pdf").mkdir()
        for digest in digests:
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("非法 PDF 哈希。")
            source = DATA / "pdf" / f"{digest}.pdf"
            target = destination / "pdf" / source.name
            shutil.copyfile(source, target)
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("备份 PDF 哈希不匹配。")
            manifest["pdf_files"].append(target.name)
        manifest["complete"] = True
        return {"path": str(destination), **manifest}
    finally:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["doctor", "backup"])
    parser.add_argument("--load-model", action="store_true")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    result = doctor(args.load_model) if args.action == "doctor" else backup(args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
