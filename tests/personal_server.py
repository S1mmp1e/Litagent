"""Isolated browser-test server; never calls an online model."""
import os
import re

import numpy as np

assert ".notebook-test-" in os.environ["LIT_DB_PATH"]
assert not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("LIT_API_KEY")

from lit_agent import database, documents, jobs, retrieval, store
from lit_agent.runtime import Guard


class TestEncoder:
    class Tokenizer:
        def encode(self, text, add_special_tokens=False):
            return [ord(c) for c in text]

        def decode(self, tokens, skip_special_tokens=True):
            return "".join(chr(c) for c in tokens)

    tokenizer = Tokenizer()

    def encode(self, texts, **kwargs):
        return np.array([[1.0] + [0.0]*383 for _ in texts], dtype=np.float32)


class TestModel:
    def __init__(self, guard):
        self.model = "offline-browser-fixture"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0}

    def complete(self, messages, on_delta=None, **kwargs):
        self.usage["model_calls"] += 1
        ids = list(dict.fromkeys(re.findall(r"\[cite:([a-f0-9-]{36})\]", messages[-1]["content"])))
        personal = list(dict.fromkeys(re.findall(r"\[personal:([a-f0-9-]{36})\]", messages[-1]["content"])))
        text = "## 原文分析\n\n软件测试回答，非真实模型评测。\n\n" + " ".join(f"[cite:{key}]" for key in ids)
        if personal:
            text += "\n\n## 我的研究条件\n\n用户批注关注局部观测。" + f"[personal:{personal[0]}]"
        if on_delta:
            on_delta(text)
        return {"role": "assistant", "content": text}

    def close(self):
        pass


def main():
    database.init_db()
    retrieval.get_embedder = lambda: TestEncoder()
    jobs.Model = TestModel
    for doc in store.rows("SELECT * FROM documents"):
        task = store.enqueue("ingest", {"paper_id": doc["paper_id"], "file_hash": doc["file_hash"], "force": True})
        documents.process(task, Guard())
        with store.connect() as db:
            db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (task["id"],))
    from lit_agent.webui import main as serve
    serve()


if __name__ == "__main__":
    main()
