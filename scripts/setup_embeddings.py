"""Explicit, potentially large download. No implicit network fetch during a user task."""

import os

os.environ["LIT_ALLOW_MODEL_DOWNLOAD"] = "1"

from lit_agent.retrieval import get_embedder
from lit_agent.settings import MODEL_NAME


if __name__ == "__main__":
    model = get_embedder()
    encoded = model.encode(["query: 论文方法", "passage: A research method."], normalize_embeddings=True)
    print({"model": MODEL_NAME, "shape": list(encoded.shape), "status": "ready"})
