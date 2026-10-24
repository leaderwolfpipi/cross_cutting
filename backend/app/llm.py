"""OpenAI-compatible chat models. Two profiles, both configured through environment variables / backend/.env:

- text   (AGENT_MODEL_*):        DeepSeek, writes copy. It reads no images.
- vision (AGENT_VISION_MODEL_*): describes images (e.g. a frame of a clip) as text the text model can use.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import get_settings

log = logging.getLogger(__name__)

DEFAULT_BASE_URLS = {"deepseek": "https://api.deepseek.com/v1", "openai": "https://api.openai.com/v1"}
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


@dataclass
class ChatModel:
    provider: str
    name: str
    base_url: str
    api_key: str
    max_attempts: int = 3

    def chat(self, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 4096,
             timeout: float = 60.0, temperature: float | None = None) -> str:
        """Returns the assistant text. Retries rate limits / 5xx / network errors; raises LLMError otherwise."""
        body: dict = {"model": self.name, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = httpx.post(url, json=body, headers=headers, timeout=timeout)
            except httpx.HTTPError as e:
                err = LLMError(f"{self.name} unreachable: {e}")
            else:
                if r.status_code < 400:
                    choice = r.json()["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise LLMError(f"{self.name} hit max_tokens")
                    return choice["message"].get("content") or ""
                err = LLMError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code not in RETRY_STATUS:
                    raise err
            if attempt < self.max_attempts:
                log.info("%s; retrying (%d/%d)", err, attempt, self.max_attempts)
                time.sleep(2 ** attempt)
        raise err


def _model(provider: str, name: str, base_url: str, api_key: str, env_prefix: str) -> ChatModel:
    base_url = base_url or DEFAULT_BASE_URLS.get(provider.lower(), "")
    missing = [k for k, v in (("NAME", name), ("BASE_URL", base_url), ("API_KEY", api_key)) if not v]
    if missing:
        raise LLMError(f"model not configured: set {', '.join(env_prefix + k for k in missing)}")
    return ChatModel(provider, name, base_url, api_key)


def text_model() -> ChatModel:
    s = get_settings()
    return _model(s.agent_model_provider, s.agent_model_name, s.agent_model_base_url, s.agent_model_api_key,
                  "AGENT_MODEL_")


def vision_model() -> ChatModel:
    s = get_settings()
    return _model(s.agent_vision_model_provider, s.agent_vision_model_name, s.agent_vision_model_base_url,
                  s.agent_vision_model_api_key, "AGENT_VISION_MODEL_")


def describe_image(image: Path | bytes, prompt: str = "用一两句中文描述画面里的海鲜和场景。",
                   mime: str = "image/jpeg") -> str:
    """Ask the vision model about one image (a file path or encoded bytes)."""
    if isinstance(image, Path):
        mime = mimetypes.guess_type(image.name)[0] or mime
        image = image.read_bytes()
    data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    return vision_model().chat([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": prompt},
    ]}], max_tokens=1024)
