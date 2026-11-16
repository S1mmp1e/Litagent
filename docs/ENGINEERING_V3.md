# LitAgent v3: evidence-grounded, personalized paper reading

Historical v3 documentation. For the 2026-11-15 reading-workflow changes, diagnostics and human-labelled evaluation tooling, see `READING_V4.md` and `VALIDATION_V4.md`.

This release keeps the existing FastAPI/SQLAlchemy/SQLite library and adds a single-user reading workspace. It is intended for local use and interview demonstrations, not for public or multi-user deployment.

## Setup

Use Python 3.11 or newer (the bundled environment used for verification was Python 3.12):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Set `DEEPSEEK_API_KEY` in the local `.env` file. Do not paste API keys in browser requests, logs, screenshots, chat exports, or documentation. A key that was ever published must be revoked at its provider console; redacting a local copy does not revoke it.

The text model calls use the server-side DeepSeek API. Install and explicitly download the local embedding model separately:

```powershell
python -m pip install -r requirements-rag.txt
$env:LIT_ALLOW_MODEL_DOWNLOAD = "1"
python -m scripts.setup_embeddings
```

The vector model is CPU-based for portable laptop use; first-time download requires network access and sufficient free disk space. The UI remains available without it, but semantic and hybrid retrieval will explain that the model is not ready. A real paper read does not use a synthetic embedding fallback.
The first-time model download is an explicit setup action and may take several minutes. Close other app processes before startup: Qdrant's local embedded storage and the SQLite database are configured for one server process.

```powershell
python -m lit_agent.webui
```

Open `http://127.0.0.1:7860`. The app stores its existing SQLite database in `lit_agent.db` and new PDF, Qdrant, and runtime artifacts under `data/`. Both are git-ignored. A SQLite backup was created under `backups/20261113_092342/` before schema migration.

For the isolated Docker deployment, first build the image, run the explicit model setup once, then start the app:

```powershell
docker compose build
docker compose --profile setup run --rm setup-embeddings
docker compose up -d litagent
```

The compose port is bound to `127.0.0.1`; the app container has no multi-user authentication and must not be exposed on a network.

## Reading flow

1. Import a local PDF or add an arXiv record in the library.
2. Select a paper and choose **处理全文**. Local uploads are retained by SHA-256; online import only downloads a syntactically valid arXiv identifier over the fixed arXiv HTTPS host.
3. pypdf extracts page text. The interface reports encrypted documents, extraction failures, scanned PDFs, and size/page limits rather than silently indexing empty content. There is no OCR, reliable formula/table understanding, or layout reconstruction.
4. Text is split using the selected embedding model's tokenizer, retaining paper, document version, page and chunk identifiers. A completed version becomes searchable only after the full vector upsert and SQLite FTS commit.
5. The reading Skills are `overview`, `deep_read`, and `compare`, each versioned in `lit_agent/reading_skills/`. Skills steer analysis but do not grant tool permissions. The profile and Skill version are snapshotted with each task.
6. Full summaries visit page-ordered chunks in bounded groups, persist completed map notes, and then synthesize. Follow-up questions retrieve evidence from the selected paper(s), using FTS5, dense Qdrant search, and reciprocal-rank fusion. Citations point to a chunk endpoint and its original PDF page.
7. A citation identifier is checked against the current supplied chunk set; this is a reference-integrity check, not a semantic entailment guarantee. Verify claims against the linked source.
8. Review notes are versioned. Only the latest explicitly approved version can be exported as JSONL for future SFT curation. Exported records include paper hashes and source prompts; they are private, potentially sensitive files and are not automatically training-ready.

## Task and chat operations

- SQLite-backed tasks/events survive page refresh; the UI reconnects to persisted SSE event IDs.
- One in-process worker serializes tasks. Concurrent work is deliberately limited to one task per session and at most 30 queued/running tasks.
- Task budget: 180 seconds by default, at most 12 tool rounds and 8 tool calls per round. Provider SDK retries are disabled to avoid replaying writes.
- On process restart, read-only jobs can resume from checkpoints. A chat task with tools is marked failed rather than replayed because a tool may already have changed the library. Verify side effects before manually restarting that request.
- User model context is persisted server-side as separate from the visible chat text. If the reading profile changes, old messages remain visible but model context starts clean. Legacy sessions display the same warning.
- Delete and retry endpoints are local user operations. There is no account or multi-user isolation.

## Endpoints

- `GET/PUT/POST /api/profile`: read, update and reset the reading profile.
- `GET /api/skills`: list the server-side Skill registry.
- `POST /api/papers/upload`: create a paper and enqueue PDF ingestion.
- `POST /api/papers/{id}/process`: enqueue arXiv PDF download or local PDF reindex.
- `GET /api/retrieval?query=...&paper_id=...&mode=keyword|dense|hybrid`: inspect retrieved chunks.
- `POST /api/chat/runs`: enqueue a reading or library-chat task; `GET /api/tasks/{id}` and `/events` observe it; `POST /api/tasks/{id}/cancel` requests cancellation.
- `GET/PUT /api/tasks/{id}/notes`: review or save a new note version; `/notes/download` downloads Markdown.
- `GET /api/exports/sft`: export only approved latest note versions.
- `GET /api/health`: report readiness and whether the server key is configured; never return the key.

## Reliability and security boundary

- Same-origin guard blocks browser cross-origin writes. The server listens only on loopback by default. Do not expose the UI to a network or configure `LIT_HOST=0.0.0.0` without adding authentication, CSRF protection, rate limits, and per-user data isolation.
- `.env`, database files, PDFs, embeddings, logs, and backups are excluded from git. SDK exception messages are replaced with generic errors; application logs never record credentials, prompts, or tool arguments.
- PDF-derived text is untrusted input. It is only included as quoted evidence and cannot authorize or alter the tool registry. This is defense-in-depth, not a guarantee against prompt injection.
- FTS5 is the keyword index. Qdrant local mode and SQLite assume one server process; do not run multiple app workers.
- A failed reindex keeps no search-visible partial version. Physical cleanup of retired Qdrant points is best-effort; current SQL visibility checks prevent retired/deleted passages from being returned.
- Keep backups outside public source control. Rotate leaked provider keys independently.

## Tests and benchmark

```powershell
python -m pytest -q
python scripts/benchmark.py
npm run test:ui
```

Tests use deterministic synthetic embeddings and generated synthetic PDFs. They validate application contracts and storage behavior, **not** multilingual-e5 quality, real-paper retrieval quality, DeepSeek answer quality, citation entailment, OCR, or task performance over a real corpus.

The human evaluation report is a separate deliverable. Curate 20 held-out real papers and 40 questions with independently checked page evidence, including unanswerable questions. Compare keyword, dense and hybrid hit@5/MRR, citation page accuracy, abstention, latency, tokens, and reviewed full-summary quality under equal inputs/model/budget. Report sample counts and measured results. Do not substitute this synthetic smoke test for that evaluation.

## Present resume evidence

Implemented code alone does not establish that the local embedding weights were downloaded, that a real PDF was successfully processed end to end, or that an online model completed a read. Record those as accomplishments only after the real setup and evaluation are run.
