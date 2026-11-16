import os
import time

import pytest

from lit_agent import retrieval


@pytest.mark.skipif(os.getenv("LIT_BENCHMARK") != "1",
                    reason="Run scripts/benchmark.py to execute the synthetic benchmark.")
def test_synthetic_retrieval_benchmark(indexed, record_property):
    paper_id, _ = indexed
    queries = [("method", "method"), ("experiment baseline", "experiment")]
    results = {}
    for mode in ("keyword", "dense", "hybrid"):
        hits_at_5 = 0
        elapsed = []
        for query, expected in queries:
            start = time.perf_counter()
            hits = retrieval.search(query, [paper_id], limit=5, mode=mode)
            elapsed.append(time.perf_counter() - start)
            if any(expected in h["text"].lower() for h in hits):
                hits_at_5 += 1
        results[f"{mode}_hit_at_5"] = hits_at_5 / len(queries)
        results[f"{mode}_mean_seconds"] = sum(elapsed) / len(elapsed)
    no_paper = retrieval.search("finding on another unrelated corpus", [paper_id + 1000])
    results["empty_scope_returns_no_evidence"] = not no_paper
    results["citation_page_accuracy"] = all(
        c["page"] in (1, 2, 3) for c in retrieval.search("method", [paper_id]))
    results["sample_count"] = len(queries) + 1
    results["synthetic_fixture"] = True
    print(results)
    record_property("benchmark", results)
    assert results["keyword_hit_at_5"] == 1.0
    assert results["empty_scope_returns_no_evidence"]
    assert results["citation_page_accuracy"]
    assert results["synthetic_fixture"]
