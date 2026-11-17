"""Local model configuration; credentials never enter API responses or task records."""

import ipaddress
import json
import os
import re
import tempfile
import threading
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from . import settings

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
_lock = threading.RLock()


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=500)
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=120)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=2048)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value):
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        hostname = url.hostname
        try:
            local = hostname == "localhost" or ipaddress.ip_address(hostname or "").is_loopback
        except ValueError:
            local = False
        if (not hostname or url.scheme not in ("http", "https")
                or (url.scheme == "http" and not local)
                or url.username is not None or url.password is not None
                or url.query or url.fragment or any(c.isspace() for c in value)
                or "\\" in value):
            raise ValueError("服务地址须为 HTTPS；本机服务可以使用 HTTP。不要包含密钥、查询参数或认证信息。")
        _ = url.port
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value):
            raise ValueError("请填写有效的模型名称。")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value):
        key = value.get_secret_value().strip()
        if any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise ValueError("密钥不能包含空格、换行或非 ASCII 字符。")
        return SecretStr(key)


def config_path():
    return settings.DATA / "model-config.json"


def environment_config():
    return ModelSettings(
        base_url=os.getenv("LIT_BASE_URL") or DEFAULT_BASE_URL,
        model=os.getenv("LIT_MODEL") or DEFAULT_MODEL,
        api_key=os.getenv("DEEPSEEK_API_KEY") or os.getenv("LIT_API_KEY") or "",
    )


def current_config():
    with _lock:
        path = config_path()
        if not path.exists():
            return environment_config()
        try:
            return ModelSettings.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("本机模型配置无法读取，请在“模型设置”中重新保存或恢复环境配置。") from error


def public_config():
    with _lock:
        local = config_path().exists()
        error = None
        try:
            config = current_config()
        except ValueError:
            config = ModelSettings()
            error = "模型配置无效，请重新填写并保存，或恢复环境配置。"
        return {
            "base_url": config.base_url,
            "model": config.model,
            "api_configured": bool(config.api_key.get_secret_value()) and error is None,
            "source": "local" if local else (
                "environment" if any(os.getenv(key) for key in
                                     ("DEEPSEEK_API_KEY", "LIT_API_KEY", "LIT_MODEL", "LIT_BASE_URL"))
                else "default"),
            "can_reset": local,
            "error": error,
        }


def resolve_draft(draft):
    if draft.api_key.get_secret_value():
        return draft
    try:
        saved = current_config()
    except ValueError:
        raise ValueError("请填写 API Key 后重新保存。") from None
    # Never forward an existing credential to a newly entered service endpoint.
    if draft.base_url != saved.base_url:
        raise ValueError("修改服务地址后，请重新填写该服务的 API Key。")
    if not saved.api_key.get_secret_value():
        raise ValueError("请先填写 API Key。")
    return draft.model_copy(update={"api_key": saved.api_key})


def save_config(draft):
    with _lock:
        config = resolve_draft(draft)
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp uses private permissions on POSIX; os.replace publishes one complete file.
        descriptor, temporary = tempfile.mkstemp(prefix=".model-config-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"base_url": config.base_url, "model": config.model,
                           "api_key": config.api_key.get_secret_value()}, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return public_config()


def reset_config():
    with _lock:
        config_path().unlink(missing_ok=True)
        return public_config()


def check_connection(draft):
    with _lock:
        config = resolve_draft(draft)
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.get(config.base_url + "/models",
                                  headers={"Authorization": "Bearer " + config.api_key.get_secret_value(),
                                           "Accept": "application/json"})
        if response.status_code in (401, 403):
            raise HTTPException(422, "认证失败，请核对服务地址和 API Key 是否匹配、密钥是否有效。")
        if response.status_code == 429:
            raise HTTPException(429, "服务商限流，请稍后重试并检查账户状态。")
        if response.status_code in (301, 302, 303, 307, 308):
            raise HTTPException(422, "服务地址发生重定向，请填写最终 API 地址后重试。")
        if response.status_code >= 400:
            raise HTTPException(502, "服务商未返回可用的模型列表，请核对 API 地址（部分服务需要 /v1）。")
        data = response.json().get("data")
        if not isinstance(data, list):
            raise ValueError("Invalid models response")
        models = list(dict.fromkeys(
            item["id"] for item in data[:500]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", item["id"])
        ))
        available = config.model in models
        return {"ok": available, "model_available": available, "models": models,
                "message": "连接成功，当前模型在可用列表中。" if available else
                "服务连接成功，但当前模型不在可用列表中，请核对模型名称。"}
    except httpx.TimeoutException:
        raise HTTPException(504, "连接超时，请检查网络和 API 服务地址。") from None
    except httpx.HTTPError:
        raise HTTPException(502, "无法连接模型服务，请检查网络和 API 服务地址。") from None
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(502, "服务返回的模型列表格式不兼容，请核对 API 地址。") from None


def settings_access(request: Request, response: Response):
    # Custom header blocks cross-site form requests. Loopback Host also blocks DNS rebinding.
    hostname = request.url.hostname
    try:
        local = hostname == "localhost" or ipaddress.ip_address(hostname or "").is_loopback
    except ValueError:
        local = False
    if not local or request.headers.get("x-litagent-settings") != "1":
        raise HTTPException(403, "请通过本机 localhost 或 127.0.0.1 页面访问模型设置。")
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/settings/model", dependencies=[Depends(settings_access)])
_request_schema = {"requestBody": {"required": True, "content": {
    "application/json": {"schema": ModelSettings.model_json_schema()}}}}


@router.get("")
def get_settings():
    return public_config()


async def read_draft(request: Request):
    try:
        return ModelSettings.model_validate(await request.json())
    except (ValidationError, ValueError):
        # FastAPI's default validation response can echo the input, including credentials.
        raise HTTPException(422, "配置格式无效，请检查 API 地址、模型名称和密钥。") from None


@router.put("", openapi_extra=_request_schema)
def put_settings(draft: ModelSettings = Depends(read_draft)):
    try:
        return save_config(draft)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except OSError:
        raise HTTPException(500, "无法保存模型配置，请检查本机数据目录的写入权限。") from None


@router.delete("")
def delete_settings():
    try:
        return reset_config()
    except OSError:
        raise HTTPException(500, "无法恢复环境配置，请检查本机数据目录的写入权限。") from None


@router.post("/test", openapi_extra=_request_schema)
def test_settings(draft: ModelSettings = Depends(read_draft)):
    try:
        return check_connection(draft)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
