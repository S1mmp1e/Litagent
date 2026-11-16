"""One-time mechanical redaction of distributed project materials; never print matches."""

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")


def main():
    changed = 0
    for path in ROOT.rglob("*"):
        if any(p in {".venv", "data", "backups", ".git"} for p in path.relative_to(ROOT).parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in {".md", ".json", ".jsonl", ".log", ".html", ".py", ".ps1", ".bat"}:
            content = path.read_text(encoding="utf-8", errors="replace")
            clean = PATTERN.sub("[REDACTED_API_KEY]", content)
            if clean != content:
                path.write_text(clean, encoding="utf-8")
                changed += 1
        elif path.suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                entries = [(item, archive.read(item)) for item in archive.infolist()]
            replaced = False
            for index, (item, value) in enumerate(entries):
                if item.filename.endswith(".xml"):
                    clean = PATTERN.sub("[REDACTED_API_KEY]", value.decode("utf-8")).encode("utf-8")
                    replaced |= clean != value
                    entries[index] = (item, clean)
            if replaced:
                temp = path.with_suffix(".redacted.tmp")
                with zipfile.ZipFile(temp, "w") as archive:
                    for item, value in entries:
                        archive.writestr(item, value)
                temp.replace(path)
                changed += 1
    print(f"Redacted files: {changed}; credentials were not printed.")


if __name__ == "__main__":
    main()
