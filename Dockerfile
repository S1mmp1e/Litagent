FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/litagent \
    LIT_HOST=0.0.0.0 \
    LIT_PORT=7860 \
    LIT_DB_PATH=/data/lit_agent.db \
    LIT_DATA_DIR=/data \
    LIT_ALLOW_MODEL_DOWNLOAD=0

WORKDIR /app
COPY requirements-rag.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-rag.txt \
    && useradd --create-home --uid 10001 litagent \
    && mkdir -p /data /home/litagent/.cache/huggingface \
    && chown -R litagent:litagent /app /data /home/litagent

COPY --chown=litagent:litagent lit_agent ./lit_agent
COPY --chown=litagent:litagent scripts ./scripts

USER litagent
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=2)"

CMD ["python", "-m", "lit_agent.webui"]
