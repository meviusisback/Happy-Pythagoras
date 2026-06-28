import os
import re
import logging

logger = logging.getLogger("agency_finder.ai_config")

PROVIDER_REGISTRY = {
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "sdk_family": "openai",
        "label": "OpenAI",
        "supports_models_endpoint": True,
        "fallback_models": ["gpt-4o-mini", "gpt-4o", "o3-mini", "gpt-4.1-nano"],
    },
    "openrouter": {
        "env_var": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "sdk_family": "openai",
        "label": "OpenRouter",
        "supports_models_endpoint": True,
        "fallback_models": ["openai/gpt-4o-mini", "openai/gpt-4o", "anthropic/claude-sonnet-4"],
    },
    "deepseek": {
        "env_var": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "sdk_family": "openai",
        "label": "DeepSeek",
        "supports_models_endpoint": True,
        "fallback_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "opencodego": {
        "env_var": "OPENCODEGO_API_KEY",
        "base_url": "https://opencode.ai/zen/go/v1",
        "sdk_family": "openai",
        "label": "OpenCode Go",
        "supports_models_endpoint": True,
        "fallback_models": [
            "minimax-m3", "minimax-m2.7", "minimax-m2.5",
            "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
            "glm-5.2", "glm-5.1", "glm-5",
            "deepseek-v4-pro", "deepseek-v4-flash",
            "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
            "mimo-v2-pro", "mimo-v2-omni", "mimo-v2.5-pro", "mimo-v2.5",
            "hy3-preview",
        ],
    },
    "claude": {
        "env_var": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "sdk_family": "anthropic",
        "label": "Claude (Anthropic)",
        "supports_models_endpoint": False,
        "fallback_models": ["claude-sonnet-4-20250514", "claude-haiku-4-20250514", "claude-opus-4-20250514"],
    },
    "gemini": {
        "env_var": "GOOGLE_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com",
        "sdk_family": "gemini",
        "label": "Google Gemini",
        "supports_models_endpoint": True,
        "fallback_models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
    },
}


def get_registered_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())


def provider_info(provider: str) -> dict:
    info = PROVIDER_REGISTRY.get(provider)
    if not info:
        raise ValueError(f"Unknown provider: {provider}")
    return info


def get_api_key(provider: str) -> str:
    return os.environ.get(provider_info(provider)["env_var"], "")


def set_api_key(provider: str, value: str) -> None:
    os.environ[provider_info(provider)["env_var"]] = value


def clear_api_key(provider: str) -> None:
    os.environ.pop(provider_info(provider)["env_var"], None)


def clear_all_api_keys() -> None:
    for p in get_registered_providers():
        clear_api_key(p)


def is_configured(provider: str) -> bool:
    return bool(get_api_key(provider))


def configured_providers() -> list[str]:
    return [p for p in get_registered_providers() if is_configured(p)]


_KEY_REDACT_PATTERN = re.compile(r"(KEY|api_key|secret|token)", re.IGNORECASE)


def redact_keys(data):
    if isinstance(data, dict):
        return {k: redact_keys(v) if isinstance(v, (dict, list)) else "***" if _KEY_REDACT_PATTERN.search(k) else v
                for k, v in data.items()}
    if isinstance(data, list):
        return [redact_keys(item) for item in data]
    return data
