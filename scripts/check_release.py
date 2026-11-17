"""Check publishable Git files without reading ignored user data or printing secrets."""

import argparse
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PARTS = {
    ".env", ".venv", ".agents", ".codex", ".claude", ".vscode", "node_modules",
    "model-config.json",
    "data", "backups", "artifacts", "__pycache__", ".pytest_cache", ".pytest-tmp",
}
SECRET_RULES = {
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "GitHub credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential URL": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
}
FAKE_SECRETS = {
    ("tests/test_maintenance_eval.py", "sk-" + "test-secret-do-not-print"),
}
LICENSES = (
    "LICENSE",
    "lit_agent/static/vendor/lucide-LICENSE",
    "lit_agent/static/vendor/marked-LICENSE",
    "lit_agent/static/vendor/dompurify-LICENSE",
    "lit_agent/static/vendor/pdfjs/LICENSE",
)


def private_path(name):
    path = Path(name)
    return (
        any(part in PRIVATE_PARTS or part.startswith((".browser-test-", ".notebook-test-"))
            for part in path.parts)
        or path.name.startswith(".env.") and name != ".env.example"
        or path.suffix.lower() in {".db", ".log", ".pem", ".key", ".p12", ".pfx", ".docx"}
        or path.name.endswith((".db-wal", ".db-shm"))
        or path.name.startswith("对话")
        or path.name.startswith(".model-config-")
        or name == "convert_jsonl_to_docx.py"
    )


def secret_findings(name, text):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in SECRET_RULES.items():
            if any((name, match.group()) not in FAKE_SECRETS for match in pattern.finditer(line)):
                findings.append(f"{name}:{number}: possible {rule}")
    return findings


def link_findings(name, text, paths):
    findings = []
    # Code examples are not document navigation; ignore their literal Markdown syntax.
    text = re.sub(r"(?ms)^(`{3,}|~{3,}).*?^\1\s*$", "", text)
    for match in re.finditer(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)", text):
        target = match.group(1).strip("<>")
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            continue
        resolved = (ROOT / Path(name).parent / unquote(url.path)).resolve()
        try:
            relative = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            findings.append(f"{name}: local link escapes repository")
            continue
        if relative not in paths:
            findings.append(f"{name}: missing published link target: {relative}")
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-untracked", action="store_true",
                        help="Check non-ignored candidate files before the first git add.")
    args = parser.parse_args()
    command = ["git", "ls-files", "-z", "--cached"]
    if args.include_untracked:
        command += ["--others", "--exclude-standard"]
    listing = subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
    paths = set(listing.stdout.decode("utf-8").strip("\0").split("\0")) - {""}
    if not paths:
        raise SystemExit("No tracked files; stage the intended files or use --include-untracked.")
    errors, total_bytes = [], 0
    for name in sorted(paths):
        if private_path(name):
            errors.append(f"{name}: private/runtime file must not be published")
            continue
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            errors.append(f"{name}: missing regular file")
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) > 10 * 1024 * 1024:
            errors.append(f"{name}: exceeds 10 MiB publication limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "\0" in text:
            continue
        errors.extend(secret_findings(name, text))
        if path.suffix.lower() == ".md":
            errors.extend(link_findings(name, text, paths))
    for license_path in LICENSES:
        if license_path not in paths:
            errors.append(f"{license_path}: missing required license file")
    if errors:
        print("\n".join(errors))
        raise SystemExit(f"Release check failed: {len(errors)} finding(s); no secret values printed.")
    print(f"Release check passed: {len(paths)} files, {total_bytes:,} bytes; "
          "private paths, common secret patterns, Markdown targets and project/vendor licenses checked.")


if __name__ == "__main__":
    main()
