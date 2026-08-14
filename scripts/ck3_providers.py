"""LLM provider definitions and endpoint safety checks.

Remote API keys are allowed only for the exact HTTPS endpoints listed here.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import urllib.parse


@dataclasses.dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    label: str
    chat_endpoint: str
    models_endpoint: str
    requires_key: bool
    remote: bool


PROVIDERS: dict[str, ProviderSpec] = {
    "local": ProviderSpec(
        "local", "ローカルLLM（無料・PC内のみ）",
        "http://127.0.0.1:1234/v1/chat/completions",
        "http://127.0.0.1:1234/v1/models",
        False, False,
    ),
    "openai": ProviderSpec(
        "openai", "OpenAI API",
        "https://api.openai.com/v1/chat/completions",
        "https://api.openai.com/v1/models",
        True, True,
    ),
    "openrouter": ProviderSpec(
        "openrouter", "OpenRouter API",
        "https://openrouter.ai/api/v1/chat/completions",
        "https://openrouter.ai/api/v1/models",
        True, True,
    ),
    "nanogpt": ProviderSpec(
        "nanogpt", "NanoGPT API（従量課金）",
        "https://nano-gpt.com/api/v1/chat/completions",
        "https://nano-gpt.com/api/v1/models",
        True, True,
    ),
    "nanogpt_subscription": ProviderSpec(
        "nanogpt_subscription", "NanoGPT API（サブスクリプション）",
        "https://nano-gpt.com/api/subscription/v1/chat/completions",
        "https://nano-gpt.com/api/v1/models",
        True, True,
    ),
}


def get_provider(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported translation provider: {provider_id}") from exc


def is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def validate_endpoint(provider_id: str, endpoint: str) -> None:
    provider = get_provider(provider_id)
    parsed = urllib.parse.urlsplit(endpoint)
    if not parsed.hostname:
        raise ValueError(f"Invalid endpoint: {endpoint}")
    if provider.provider_id == "local":
        if parsed.scheme not in {"http", "https"} or not is_loopback_host(parsed.hostname):
            raise ValueError("Local LLM mode accepts only localhost, 127.0.0.0/8, or ::1")
        return
    canonical = urllib.parse.urlsplit(provider.chat_endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname.lower() != canonical.hostname.lower()
        or parsed.port != canonical.port
        or parsed.path.rstrip("/") != canonical.path.rstrip("/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"{provider.label}のAPIキーは公式エンドポイント以外へ送信できません: {provider.chat_endpoint}")


def models_endpoint(provider_id: str, chat_endpoint: str) -> str:
    provider = get_provider(provider_id)
    validate_endpoint(provider_id, chat_endpoint)
    if provider.provider_id != "local":
        return provider.models_endpoint
    parsed = urllib.parse.urlsplit(chat_endpoint)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/v1/models", "", ""))
