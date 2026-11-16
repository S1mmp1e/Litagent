import io
import os
import re
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Never let tests touch the user's library or credentials.
SANDBOX = Path(tempfile.mkdtemp(prefix="litagent-tests-"))
os.environ["LIT_DB_PATH"] = str(SANDBOX / "test.db")
os.environ["LIT_DATA_DIR"] = str(SANDBOX / "data")
os.environ["LIT_ALLOW_MODEL_DOWNLOAD"] = "0"

from lit_agent import api, database
from lit_agent import documents, retrieval, settings, skills, store


class Tokenizer:
    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]

    def decode(self, tokens, skip_special_tokens=True):
        return "".join(chr(i) for i in tokens)


class FakeEmbedder:
    """Synthetic vectors test transport/ranking, not real embedding quality."""
    tokenizer = Tokenizer()

    def encode(self, texts, **kwargs):
        result = []
        for text in texts:
            vector = np.zeros(384, dtype=np.float32)
            vector[0] = 1
            vector[1] = 4 if "method" in text.lower() else 0
            vector[2] = 4 if "experiment" in text.lower() else 0
            result.append(vector / np.linalg.norm(vector))
        return np.array(result)


class FakeModel:
    def __init__(self, guard=None):
        self.guard = guard
        self.model = "synthetic-test-model"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0}
        self.calls = []

    def complete(self, messages, tools=None, on_delta=None, max_tokens=2500):
        self.calls.append(messages)
        self.usage["model_calls"] += 1
        self.usage["prompt_tokens"] += 100
        self.usage["completion_tokens"] += 30
        ids = list(dict.fromkeys(re.findall(r"\[cite:([a-f0-9-]{36})\]", messages[-1]["content"])))
        content = "Synthetic test answer, not a real paper evaluation. " + " ".join(f"[cite:{i}]" for i in ids)
        if on_delta:
            on_delta(content)
        return {"role": "assistant", "content": content}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def clean_database(tmp_path, monkeypatch):
    database.init_db()
    retrieval.close_vectors()
    for module in (settings, documents, retrieval, api):
        monkeypatch.setattr(module, "DATA", tmp_path / "data")
    monkeypatch.setattr(retrieval, "get_embedder", lambda: FakeEmbedder())
    with store.connect() as db:
        db.execute("DELETE FROM note_templates WHERE builtin=0")
        for table in ("personal_fts", "personal_chunks", "notes", "task_events", "tasks", "reading_cache", "chunks_fts", "chunks", "pages", "documents",
                      "paper_tags", "paper_categories", "papers", "tags", "categories", "chat_sessions"):
            db.execute(f"DELETE FROM {table}")
    skills.save_profile({"background": "", "purpose": "理解论文方法与实验", "focus": "", "depth": "standard", "language": "中文"})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LIT_API_KEY", raising=False)
    yield
    retrieval.close_vectors()


@pytest.fixture
def pdf_bytes():
    from reportlab.pdfgen import canvas
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, invariant=1)
    for page in range(1, 4):
        pdf.drawString(60, 790, f"SYNTHETIC TEST PAPER - Page {page}")
        pdf.drawString(60, 760, "The method uses graph representations for decision making.")
        pdf.drawString(60, 730, "The experiment compares a baseline under fixed evaluation conditions.")
        pdf.drawString(60, 700, "This document is a software test fixture, not scientific evidence.")
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


@pytest.fixture
def indexed(pdf_bytes):
    from lit_agent.runtime import Guard
    paper_id, digest = documents.upload(pdf_bytes, "Synthetic test paper")
    task = store.enqueue("ingest", {"paper_id": paper_id, "file_hash": digest})
    result = documents.process(task, Guard())
    with store.connect() as db:
        db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?", (store.dumps(result), task["id"]))
    return paper_id, result
