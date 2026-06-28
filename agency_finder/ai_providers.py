import asyncio
import time
import logging
from typing import Optional, List, Type

try:
    from pydantic import BaseModel
    _PYDANTIC_AVAILABLE = True
except ImportError:
    class BaseModel:
        pass
    _PYDANTIC_AVAILABLE = False

from .ai_config import get_api_key, provider_info

logger = logging.getLogger("agency_finder.ai_providers")

_MODEL_CACHE: dict[str, tuple[float, list[str]]] = {}
_MODEL_CACHE_TTL = 300


class AIError(Exception):
    def __init__(self, provider: str, message: str, status_code: Optional[int] = None):
        self.provider = provider
        self.status_code = status_code
        msg = f"[{provider}] {message}" if status_code is None else f"[{provider}] HTTP {status_code}: {message}"
        super().__init__(msg)


def _get_openai_client(provider: str, timeout: int = 30):
    from openai import AsyncOpenAI
    info = provider_info(provider)
    key = get_api_key(provider)
    base_url = info["base_url"]
    if not base_url and provider == "opencodego":
        base_url = "https://api.opencode.ai/v1"
    return AsyncOpenAI(api_key=key, base_url=base_url, timeout=timeout, max_retries=0)


def _get_anthropic_client(provider: str, timeout: int = 30):
    from anthropic import AsyncAnthropic
    key = get_api_key(provider)
    return AsyncAnthropic(api_key=key, timeout=timeout, max_retries=0)


def _get_gemini_client(provider: str):
    from google import genai
    key = get_api_key(provider)
    return genai.aio.Client(api_key=key)


async def achat(provider: str, model: str, messages: list, *,
                system: Optional[str] = None, json_mode: bool = False,
                timeout: int = 30, max_retries: int = 1) -> str:
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await _achat_single(provider, model, messages, system=system, json_mode=json_mode, timeout=timeout)
        except AIError as e:
            if e.status_code == 429 and attempt < max_retries:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Rate limited on {provider}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                last_error = e
                continue
            raise
        except Exception as e:
            logger.error(f"Unexpected error from {provider}: {e}")
            raise AIError(provider, str(e))
    if last_error:
        raise last_error


async def _achat_single(provider: str, model: str, messages: list, *,
                        system: Optional[str], json_mode: bool, timeout: int) -> str:
    info = provider_info(provider)
    family = info["sdk_family"]

    if family == "openai":
        return await _achat_openai(provider, model, messages, system=system, json_mode=json_mode, timeout=timeout)
    elif family == "anthropic":
        return await _achat_anthropic(provider, model, messages, system=system, json_mode=json_mode, timeout=timeout)
    elif family == "gemini":
        return await _achat_gemini(provider, model, messages, system=system, json_mode=json_mode, timeout=timeout)
    else:
        raise AIError(provider, f"Unknown SDK family: {family}")


async def _achat_openai(provider: str, model: str, messages: list, *,
                        system: Optional[str], json_mode: bool, timeout: int) -> str:
    client = _get_openai_client(provider, timeout)
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    kwargs: dict = dict(model=model, messages=msgs, timeout=timeout)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = await client.chat.completions.create(**kwargs)
        if isinstance(response, str):
            logger.debug(f"{provider} returned a raw string response")
            return response
        return response.choices[0].message.content or ""
    except Exception as e:
        status = getattr(e, "status_code", None)
        raise AIError(provider, str(e), status_code=status)


async def _achat_anthropic(provider: str, model: str, messages: list, *,
                           system: Optional[str], json_mode: bool, timeout: int) -> str:
    client = _get_anthropic_client(provider, timeout)
    anthropic_messages = []
    for m in messages:
        role = "user" if m.get("role") == "user" else "assistant"
        content = m.get("content", "")
        anthropic_messages.append({"role": role, "content": content})

    system_text = system
    if json_mode:
        hint = "\nYou MUST return ONLY valid JSON. No markdown, no code fences."
        system_text = (system_text or "") + hint

    try:
        response = await client.messages.create(
            model=model,
            system=system_text or "",
            messages=anthropic_messages,
            max_tokens=4096,
        )
        return response.content[0].text
    except Exception as e:
        status = getattr(e, "status_code", None)
        raise AIError(provider, str(e), status_code=status)


async def _achat_gemini(provider: str, model: str, messages: list, *,
                        system: Optional[str], json_mode: bool, timeout: int) -> str:
    client = _get_gemini_client(provider)
    from google.genai import types

    parts = []
    if system:
        parts.append(f"[System]\n{system}")
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            parts.append(f"[User]\n{content}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
    contents = "\n\n".join(parts)

    config = types.GenerateContentConfig(
        max_output_tokens=4096,
        temperature=0.1,
    )
    if json_mode:
        config.response_mime_type = "application/json"

    try:
        response = await client.models.generate_content_async(
            model=model,
            contents=contents,
            config=config,
        )
        return response.text
    except Exception as e:
        raise AIError(provider, str(e))


async def achat_json(provider: str, model: str, messages: list, *,
                     schema: Type[BaseModel], system: Optional[str] = None,
                     timeout: int = 30) -> BaseModel:
    info = provider_info(provider)
    family = info["sdk_family"]

    if family == "openai":
        return await _achat_json_openai(provider, model, messages, schema=schema, system=system, timeout=timeout)
    else:
        schema_desc = _schema_to_description(schema)
        sys_text = (system or "") + "\n\nReturn valid JSON matching this schema:\n" + schema_desc
        raw = await achat(provider, model, messages, system=sys_text, json_mode=True, timeout=timeout)
        return schema.model_validate_json(raw)


async def _achat_json_openai(provider: str, model: str, messages: list, *,
                             schema: Type[BaseModel], system: Optional[str],
                             timeout: int) -> BaseModel:
    client = _get_openai_client(provider, timeout)
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=msgs,
            timeout=timeout,
            response_format={
                "type": "json_object",
            },
        )
        if isinstance(response, str):
            raw = response
        else:
            raw = response.choices[0].message.content or ""
        return schema.model_validate_json(raw)
    except Exception as e:
        status = getattr(e, "status_code", None)
        raise AIError(provider, str(e), status_code=status)


def _schema_to_description(schema: Type[BaseModel]) -> str:
    import json
    return json.dumps(schema.model_json_schema(), indent=2, ensure_ascii=False)


async def alist_models(provider: str, *, timeout: int = 10) -> list[str]:
    now = time.time()
    cached = _MODEL_CACHE.get(provider)
    if cached and now - cached[0] < _MODEL_CACHE_TTL:
        return cached[1]

    info = provider_info(provider)
    family = info["sdk_family"]
    supports_endpoint = info.get("supports_models_endpoint", True)

    if family == "openai" and supports_endpoint:
        models = await _alist_models_openai(provider, timeout)
    elif family == "gemini" and supports_endpoint:
        models = await _alist_models_gemini(provider, timeout)
    else:
        models = list(info.get("fallback_models", []))

    _MODEL_CACHE[provider] = (time.time(), models)
    return models


async def _alist_models_openai(provider: str, timeout: int) -> list[str]:
    from .ai_config import get_api_key
    if not get_api_key(provider):
        return list(provider_info(provider).get("fallback_models", []))
    client = _get_openai_client(provider, timeout)
    try:
        response = await client.models.list()
        models = sorted(m.id for m in response.data if not m.id.startswith("ft:"))
        return models if models else list(provider_info(provider).get("fallback_models", []))
    except Exception as e:
        logger.warning(f"Models endpoint failed for {provider}: {e}")
        return list(provider_info(provider).get("fallback_models", []))


async def _alist_models_gemini(provider: str, timeout: int) -> list[str]:
    client = _get_gemini_client(provider)
    models = []
    try:
        async for model in client.models.list():
            name = model.name.replace("models/", "")
            if "gemini" in name:
                models.append(name)
        return sorted(models) if models else list(provider_info(provider).get("fallback_models", []))
    except Exception as e:
        logger.warning(f"Gemini models endpoint failed: {e}")
        return list(provider_info(provider).get("fallback_models", []))


def clear_model_cache() -> None:
    _MODEL_CACHE.clear()
