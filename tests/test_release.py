from scripts.check_release import link_findings, private_path, secret_findings


def test_private_release_paths():
    for name in (".env", ".env.local", "data/pdf/source.pdf", "backups/library.db",
                 ".notebook-test-demo/test.db", "docs/对话记录.md", "private.key"):
        assert private_path(name)
    assert not private_path(".env.example")
    assert not private_path("lit_agent/database.py")


def test_secret_values_never_appear_in_diagnostics():
    credential = "sk-" + "X" * 32
    findings = secret_findings("README.md", credential)
    assert findings and credential not in "\n".join(findings)
    fixture_secret = "sk-" + "test-secret-do-not-print"
    assert not secret_findings("tests/test_maintenance_eval.py", fixture_secret)
    assert secret_findings("README.md", fixture_secret)


def test_links_require_published_files():
    paths = {"docs/guide.md", "docs/images/example.png"}
    text = "[Guide](guide.md#usage)\n![View](images/example.png)\n[Missing](missing.md)"
    findings = link_findings("docs/guide.md", text, paths)
    assert len(findings) == 1 and "missing.md" in findings[0]
    assert not link_findings("README.md", "```md\n[x](missing.md)\n```\n[x](https://example.com)", paths)
