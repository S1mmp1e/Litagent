import json
import os
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from lit_agent import model_config, runtime, settings
from lit_agent.webui import app

KEY = "synthetic-settings-test-key"
ENDPOINT = "/api/settings/model"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LIT_BASE_URL", model_config.DEFAULT_BASE_URL)
    monkeypatch.setenv("LIT_MODEL", model_config.DEFAULT_MODEL)
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False,
                      headers={"X-LitAgent-Settings": "1"})


def draft(**values):
    return {"base_url": model_config.DEFAULT_BASE_URL, "model": model_config.DEFAULT_MODEL,
            "api_key": KEY, **values}


def mock_service(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(model_config.httpx, "Client",
                        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))


def test_save_reload_and_runtime_use_one_config_without_returning_key(client, monkeypatch):
    assert not client.get(ENDPOINT).json()["api_configured"]
    response = client.put(ENDPOINT, json=draft(model="my-model"))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert KEY not in response.text and "api_key" not in response.json()
    assert response.json()["source"] == "local"
    assert client.get("/api/health").json()["model"] == "my-model"
    model = runtime.Model(runtime.Guard())
    try:
        assert model.model == "my-model" and model.client.api_key == KEY
    finally:
        model.close()
    # A fresh process must read the saved file, independently of in-process state.
    env = {**os.environ, "LIT_DATA_DIR": str(settings.DATA), "DEEPSEEK_API_KEY": "", "LIT_API_KEY": ""}
    result = subprocess.run([sys.executable, "-c",
                             "import json; from lit_agent.model_config import public_config; "
                             "print(json.dumps(public_config()))"],
                            cwd=settings.ROOT, env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["model"] == "my-model"
    assert KEY not in result.stdout + result.stderr
    assert model_config.config_path().is_file()


def test_blank_key_keeps_saved_key_and_reset_restores_environment(client, monkeypatch):
    environment_key = "synthetic-environment-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", environment_key)
    assert client.get(ENDPOINT).json()["source"] == "environment"
    assert client.put(ENDPOINT, json=draft()).status_code == 200
    assert client.put(ENDPOINT, json=draft(api_key="", model="other-model")).status_code == 200
    assert model_config.current_config().api_key.get_secret_value() == KEY
    restored = client.delete(ENDPOINT).json()
    assert restored["source"] == "environment" and restored["api_configured"]
    assert model_config.current_config().api_key.get_secret_value() == environment_key
    assert os.getenv("DEEPSEEK_API_KEY") == environment_key
    assert not model_config.config_path().exists()
    assert environment_key not in client.get(ENDPOINT).text


def test_reset_without_environment_key_returns_to_setup(client):
    client.put(ENDPOINT, json=draft())
    assert not client.delete(ENDPOINT).json()["api_configured"]
    assert not client.get("/api/health").json()["api_configured"]


def test_base_url_change_requires_new_key_even_for_connection_test(client, monkeypatch):
    client.put(ENDPOINT, json=draft())
    calls = []
    mock_service(monkeypatch, lambda request: calls.append(request))
    changed = draft(base_url="https://other.example/v1", api_key="")
    assert client.put(ENDPOINT, json=changed).status_code == 422
    assert client.post(ENDPOINT + "/test", json=changed).status_code == 422
    assert not calls
    assert model_config.current_config().base_url == model_config.DEFAULT_BASE_URL
    assert client.put(ENDPOINT, json={**changed, "api_key": "new-service-key"}).status_code == 200


@pytest.mark.parametrize("changes", [
    {"base_url": "http://remote.example"},
    {"base_url": "https://" + "user:password@example.com"},
    {"base_url": "https://api.deepseek.com?key=" + KEY},
    {"base_url": "https://api.deepseek.com/#fragment"},
    {"base_url": "https://api.deepseek.com:invalid"},
    {"base_url": "file:///etc/passwd"},
    {"base_url": "https://api.deepseek.com\\@evil.example"},
    {"model": "<script>alert(1)</script>"},
    {"api_key": KEY + "\nsecond-line"},
    {"api_key": "x" * 2049},
    {"extra": KEY},
])
def test_invalid_settings_never_echo_request_credentials(client, changes):
    response = client.put(ENDPOINT, json=draft(**changes))
    assert response.status_code == 422
    assert KEY not in response.text and "input" not in response.json()
    assert not model_config.config_path().exists()


def test_local_http_endpoint_is_supported(client):
    assert client.put(ENDPOINT, json=draft(base_url="http://127.0.0.1:8080/v1")).status_code == 200


def test_missing_header_foreign_host_and_cross_origin_are_rejected(client):
    assert client.get(ENDPOINT, headers={"X-LitAgent-Settings": ""}).status_code == 403
    assert client.get("http://evil.example" + ENDPOINT).status_code == 403
    assert client.put(ENDPOINT, json=draft(), headers={"Origin": "https://evil.example"}).status_code == 403
    assert not model_config.config_path().exists()


def test_connection_test_queries_models_only_and_does_not_save(client, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url == model_config.DEFAULT_BASE_URL + "/models"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert not request.content
        return httpx.Response(200, json={"data": [{"id": model_config.DEFAULT_MODEL}, {"id": "other-model"}]})

    mock_service(monkeypatch, handler)
    response = client.post(ENDPOINT + "/test", json=draft())
    assert response.status_code == 200 and response.json()["ok"]
    assert KEY not in response.text
    assert len(requests) == 1 and not model_config.config_path().exists()
    missing = client.post(ENDPOINT + "/test", json=draft(model="unknown"))
    assert missing.status_code == 200 and not missing.json()["model_available"]


@pytest.mark.parametrize("code,expected", [(401, 422), (403, 422), (429, 429), (404, 502), (500, 502), (302, 422)])
def test_provider_errors_and_redirects_do_not_leak_secrets(client, monkeypatch, code, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(code, text=KEY, headers={"Location": "https://other.example/models"})

    mock_service(monkeypatch, handler)
    response = client.post(ENDPOINT + "/test", json=draft())
    assert response.status_code == expected and KEY not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize("error_type,expected", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
def test_network_errors_are_sanitized(client, monkeypatch, error_type, expected):
    def handler(request):
        raise error_type("request contained " + KEY, request=request)

    mock_service(monkeypatch, handler)
    response = client.post(ENDPOINT + "/test", json=draft())
    assert response.status_code == expected and KEY not in response.text


def test_failed_atomic_save_preserves_previous_config(client, monkeypatch):
    client.put(ENDPOINT, json=draft())
    before = model_config.config_path().read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic disk failure " + KEY)

    monkeypatch.setattr(model_config.os, "replace", fail_replace)
    response = client.put(ENDPOINT, json=draft(api_key="replacement-key"))
    assert response.status_code == 500 and KEY not in response.text
    assert model_config.config_path().read_bytes() == before
    assert not list(settings.DATA.glob(".model-config-*.tmp"))


def test_invalid_saved_config_can_be_recovered_without_silent_fallback(client):
    settings.DATA.mkdir(parents=True, exist_ok=True)
    model_config.config_path().write_text("invalid synthetic config", encoding="utf-8")
    response = client.get(ENDPOINT).json()
    assert response["error"] and not response["api_configured"] and response["can_reset"]
    with pytest.raises(ValueError, match="模型配置"):
        runtime.Model(runtime.Guard())
    assert client.put(ENDPOINT, json=draft(api_key="")).status_code == 422
    assert client.put(ENDPOINT, json=draft()).status_code == 200


@pytest.mark.parametrize("base_url,expected", [
    ("https://api.deepseek.com", {"thinking": {"type": "disabled"}}),
    ("https://other.example/v1", None),
])
def test_model_calls_keep_non_thinking_budget_only_for_official_deepseek(client, monkeypatch, base_url, expected):
    client.put(ENDPOINT, json=draft(base_url=base_url, model="deepseek-flash"))
    model = runtime.Model(runtime.Guard())
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(model_dump=lambda **_: {"role": "assistant", "content": "fixture"}))])

    monkeypatch.setattr(model.client.chat.completions, "create", complete)
    try:
        model.complete([{"role": "user", "content": "fixture"}])
        assert calls[0].get("extra_body") == expected
    finally:
        model.close()
