import os
import hashlib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)
DATA = Path(os.getenv("LIT_DATA_DIR", str(ROOT / "data"))).resolve()
DB_FILE = Path(os.getenv("LIT_DB_PATH", str(ROOT / "lit_agent.db"))).resolve()
if os.getenv("LIT_DB_PATH") is None:
    legacy = ROOT / "lit_agent.db"
    if legacy.exists() and legacy.is_file():
        DB_FILE = legacy
MODEL_NAME = "intfloat/multilingual-e5-small"
PARSER_VERSION = "pypdf-page-v1"
INDEX_VERSION = "e5-small-350-40-v1"
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_TASK_SECONDS = int(os.getenv("LIT_TASK_SECONDS", "180"))
MAX_READING_SECONDS = int(os.getenv("LIT_READING_SECONDS", "600"))
READING_MAX_OUTPUT_TOKENS = int(os.getenv("LIT_READING_MAX_TOKENS", "8192"))
MAX_ROUNDS = 12


def library_identity():
    paths = [os.path.normcase(str(path)) for path in (DB_FILE, DATA)]
    return hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


def prepare_dirs():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "pdf").mkdir(exist_ok=True)
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
