"""Version-filtered lexical/dense retrieval. SQL remains the source of truth."""

import os
import re
import threading
from functools import lru_cache

from . import store
from .settings import DATA, MODEL_NAME, prepare_dirs

COLLECTION = "multilingual_e5_small_v1"
_lock = threading.RLock()


@lru_cache(maxsize=1)
def get_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(
            MODEL_NAME, device="cpu",
            local_files_only=os.getenv("LIT_ALLOW_MODEL_DOWNLOAD", "0") != "1",
        )
    except (ImportError, OSError) as error:
        raise ValueError("语义模型未就绪。安装 requirements-rag.txt 并运行 python -m scripts.setup_embeddings。") from error


@lru_cache(maxsize=1)
def vector_client():
    from qdrant_client import QdrantClient

    prepare_dirs()
    return QdrantClient(path=str(DATA / "vectors"))


def close_vectors():
    if vector_client.cache_info().currsize:
        vector_client().close()
        vector_client.cache_clear()


def split_page(text):
    """Use the actual E5 tokenizer, including room for the passage prefix."""
    tokenizer = get_embedder().tokenizer
    tokens = tokenizer.encode(text, add_special_tokens=False)
    for offset in range(0, len(tokens), 310):
        yield tokenizer.decode(tokens[offset:offset + 350], skip_special_tokens=True)


def index_chunks(chunks):
    from qdrant_client import models

    if not chunks:
        raise ValueError("PDF 中未提取到可索引文本。")
    model = get_embedder()
    with _lock:
        client = vector_client()
        if not client.collection_exists(COLLECTION):
            client.create_collection(COLLECTION, vectors_config=models.VectorParams(
                size=384, distance=models.Distance.COSINE,
            ))
        for offset in range(0, len(chunks), 32):
            batch = chunks[offset:offset + 32]
            vectors = model.encode(["passage: " + c["text"] for c in batch],
                                   normalize_embeddings=True, show_progress_bar=False)
            client.upsert(COLLECTION, points=[
                models.PointStruct(id=c["id"], vector=v.tolist(), payload={
                    "document_id": c["document_id"], "paper_id": c["paper_id"],
                }) for c, v in zip(batch, vectors)
            ], wait=True)


def purge_documents(document_ids):
    if not document_ids:
        return
    from qdrant_client import models

    with _lock:
        client = vector_client()
        if client.collection_exists(COLLECTION):
            client.delete(COLLECTION, points_selector=models.FilterSelector(filter=models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids))]
            )), wait=True)


def search(query, paper_ids=None, limit=8, mode="hybrid"):
    if mode not in ("keyword", "dense", "hybrid"):
        raise ValueError("未知检索模式。")
    docs = store.rows("SELECT id,paper_id FROM documents WHERE status='ready'")
    active = [d["id"] for d in docs if not paper_ids or d["paper_id"] in paper_ids]
    if not active:
        return []
    placeholders = ",".join("?" for _ in active)
    lexical, dense = [], []
    if mode != "dense":
        # Treat user terms as literals, not executable FTS syntax.
        terms = re.findall(r"\w+", query, flags=re.UNICODE)[:32]
        if terms:
            expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
            lexical = store.rows(f"""
              SELECT c.*,bm25(chunks_fts) AS score FROM chunks_fts
              JOIN chunks c ON c.id=chunks_fts.chunk_id
              WHERE chunks_fts MATCH ? AND c.document_id IN ({placeholders})
              ORDER BY score LIMIT 40""", (expression, *active))
    if mode != "keyword":
        from qdrant_client import models

        vector = get_embedder().encode(["query: " + query], normalize_embeddings=True,
                                      show_progress_bar=False)[0].tolist()
        with _lock:
            client = vector_client()
            if client.collection_exists(COLLECTION):
                hits = client.query_points(
                    COLLECTION, query=vector, limit=40,
                    query_filter=models.Filter(must=[models.FieldCondition(
                        key="document_id", match=models.MatchAny(any=active),
                    )]),
                ).points
                dense = [{"id": str(p.id)} for p in hits]
    scores = {}
    for ranking in (lexical, dense):
        for rank, item in enumerate(ranking, 1):
            scores[item["id"]] = scores.get(item["id"], 0) + 1 / (60 + rank)
    result = []
    for chunk_id in sorted(scores, key=scores.get, reverse=True):
        # Recheck visibility after vector search, including concurrent deletion/reindex.
        chunk = store.one("""SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id
                             WHERE c.id=? AND d.status='ready'""", (chunk_id,))
        if chunk:
            chunk["score"] = scores[chunk_id]
            result.append(chunk)
        if len(result) == limit:
            break
    return result


def with_neighbors(chunks, limit=16):
    """Keep all primary hits before adding nearby fragments from the same active page."""
    result, seen = [], set()
    for chunk in chunks:
        if chunk["id"] not in seen:
            result.append(chunk)
            seen.add(chunk["id"])
    for chunk in list(result):
        nearby = store.rows("""SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE c.document_id=? AND c.page=? AND abs(c.ordinal-?)=1 AND d.status='ready'
            ORDER BY c.ordinal""", (chunk["document_id"], chunk["page"], chunk["ordinal"]))
        for item in nearby:
            if len(result) >= limit:
                return result[:limit]
            if item["id"] not in seen:
                seen.add(item["id"])
                result.append(item)
    return result[:limit]
