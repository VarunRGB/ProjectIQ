"""Thin client for a local Ollama server.

Every call asks for JSON that matches a Pydantic model. Ollama enforces the
schema through its `format` field; if the model still returns something that
does not validate, we show it the error and ask once more."""
import json
import re
from typing import Optional, Type, TypeVar
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class LLMError(RuntimeError):
    pass


def parse_model(content: str, model: Type[T]) -> T:
    """Pull a JSON object out of raw model output and validate it."""
    text = _THINK.sub("", content or "").strip()
    text = _FENCE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the model output")
    data = json.loads(text[start:end + 1])
    return model.model_validate(data)


class OllamaClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.base = settings.ollama_url.rstrip("/")
        self.http = requests.Session()
        # A local Ollama must never be reached through a system proxy.
        if urlparse(self.base).hostname in ("localhost", "127.0.0.1", "::1"):
            self.http.trust_env = False

    # ------------------------------------------------------------------
    def check(self) -> None:
        """Fail early, with a clear message, if Ollama or the model is missing."""
        try:
            r = self.http.get(f"{self.base}/api/tags", timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LLMError(
                f"Cannot reach Ollama at {self.base}. Start the Ollama app "
                f"(or run `ollama serve`) and try again. Details: {e}") from e
        names = {m.get("name", "") for m in r.json().get("models", [])}
        want = self.s.model
        if want not in names and f"{want}:latest" not in names:
            have = ", ".join(sorted(names)) or "none"
            raise LLMError(
                f"Model '{want}' is not downloaded. Run:  ollama pull {want}\n"
                f"Models currently available: {have}")

    # ------------------------------------------------------------------
    def chat_json(self, system: str, user: str, schema_model: Type[T],
                  temperature: float = 0.0, seed: Optional[int] = None) -> T:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        last_err = None
        for _ in range(2):
            content = self._post(messages, schema_model, temperature, seed)
            try:
                return parse_model(content, schema_model)
            except (ValueError, ValidationError) as e:
                last_err = e
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content":
                        f"That reply was not valid JSON for the required schema "
                        f"({e}). Reply again with only the JSON object."}]
        raise LLMError(f"Model did not return valid JSON after a retry: {last_err}")

    def _post(self, messages, schema_model, temperature, seed) -> str:
        options = {"temperature": temperature, "num_ctx": self.s.num_ctx}
        if seed is not None:
            options["seed"] = seed
        payload = {
            "model": self.s.model,
            "messages": messages,
            "stream": False,
            "format": schema_model.model_json_schema(),
            "options": options,
            "keep_alive": "15m",
        }
        # Reasoning models (Qwen3, DeepSeek-R1) spend tokens "thinking" unless
        # told not to. Only send the flag to models that understand it.
        if any(k in self.s.model.lower() for k in ("qwen3", "deepseek-r1")):
            payload["think"] = False
        try:
            r = self.http.post(f"{self.base}/api/chat", json=payload,
                              timeout=self.s.timeout_s)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        return r.json().get("message", {}).get("content", "")
