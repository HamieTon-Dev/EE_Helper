"""AI provider adapters. Each adapter takes text + images and returns a JSON object.

Supported:
- ``anthropic``: Claude via the official Anthropic SDK (structured JSON output, adaptive
  thinking, server-side refusal fallback).
- ``openai``: ChatGPT models via the official OpenAI SDK (JSON-schema response format).
- ``openai_compatible``: any endpoint speaking the OpenAI Chat Completions protocol
  (Azure OpenAI, OpenRouter, a local LM Studio/Ollama server, ...). Set ``base_url``.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Callable, Protocol

from mscopilot.inputs import ImageInput
from mscopilot.plan import extract_json_object

Log = Callable[[str], None]


class AIError(RuntimeError):
    pass


@dataclass
class Prompt:
    system: str
    text: str
    images: list[ImageInput]
    schema: dict
    schema_name: str


class Provider(Protocol):
    name: str

    def complete_json(self, prompt: Prompt) -> dict: ...


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


# ---------------------------------------------------------------- Anthropic
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "high", log: Log = print, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=900, max_retries=3)
        self.client = client
        self.model = model
        self.effort = effort
        self.log = log

    def _content(self, prompt: Prompt) -> list[dict]:
        content: list[dict] = []
        for img in prompt.images:
            content.append({"type": "text", "text": f"Image: {img.name}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": _b64(img.data)}})
        content.append({"type": "text", "text": prompt.text})
        return content

    def complete_json(self, prompt: Prompt) -> dict:
        import anthropic

        kwargs = dict(
            model=self.model,
            max_tokens=64000,
            system=prompt.system,
            messages=[{"role": "user", "content": self._content(prompt)}],
            thinking={"type": "adaptive"},
            # Refusal fallback: if a safety classifier declines, the server retries on a
            # suitable fallback model instead of returning an empty answer.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        output_config = {"effort": self.effort, "format": {"type": "json_schema", "schema": prompt.schema}}
        try:
            msg = self._stream(output_config=output_config, **kwargs)
        except anthropic.BadRequestError as e:
            # Some accounts/models reject a schema feature; fall back to free-form JSON.
            self.log(f"Structured output rejected ({e.message}); retrying with plain JSON instructions.")
            kwargs["system"] = prompt.system + "\n\nRespond with only the JSON object. Schema:\n" + json.dumps(prompt.schema)
            msg = self._stream(output_config={"effort": self.effort}, **kwargs)

        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise AIError(f"The model declined the request ({getattr(details, 'category', None) or 'refusal'}).")
        if msg.stop_reason == "max_tokens":
            raise AIError("The model's answer was cut off (max_tokens). Try simplifying the brief.")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        if not text.strip():
            raise AIError("The model returned no text.")
        try:
            return json.loads(text)
        except ValueError:
            return extract_json_object(text)

    def _stream(self, **kwargs):
        with self.client.beta.messages.stream(**kwargs) as stream:
            return stream.get_final_message()


# ---------------------------------------------------------------- OpenAI / compatible
class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "", log: Log = print, client=None):
        if not model:
            raise AIError("Set a model name for the OpenAI-compatible provider in Settings.")
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key or "not-needed", base_url=base_url or None, timeout=900, max_retries=3)
        self.client = client
        self.model = model
        self.compatible = bool(base_url)
        self.log = log

    def _messages(self, prompt: Prompt) -> list[dict]:
        content: list[dict] = [{"type": "text", "text": prompt.text}]
        for img in prompt.images:
            content.append({"type": "text", "text": f"Image: {img.name}"})
            content.append({"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{_b64(img.data)}"}})
        return [{"role": "system", "content": prompt.system}, {"role": "user", "content": content}]

    def complete_json(self, prompt: Prompt) -> dict:
        formats = [
            {"type": "json_schema", "json_schema": {"name": prompt.schema_name, "schema": prompt.schema, "strict": True}},
            {"type": "json_object"},
            None,
        ]
        last_err: Exception | None = None
        for fmt in formats:
            messages = self._messages(prompt)
            if fmt is None or fmt["type"] == "json_object":
                messages[0]["content"] += "\n\nRespond with only the JSON object. Schema:\n" + json.dumps(prompt.schema)
            kwargs = {"model": self.model, "messages": messages}
            if fmt is not None:
                kwargs["response_format"] = fmt
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as e:  # unsupported response_format on this endpoint -> try next
                last_err = e
                self.log(f"Endpoint rejected response format {fmt and fmt['type']}: {e}")
                continue
            choice = resp.choices[0]
            refusal = getattr(choice.message, "refusal", None)
            if refusal:
                raise AIError(f"The model declined the request: {refusal}")
            text = choice.message.content or ""
            if choice.finish_reason == "length":
                raise AIError("The model's answer was cut off (length). Try simplifying the brief.")
            try:
                return json.loads(text)
            except ValueError:
                return extract_json_object(text)
        raise AIError(f"AI request failed: {last_err}")


def make_provider(settings, api_key: str, log: Log = print) -> Provider:
    if settings.provider == "anthropic":
        if not api_key:
            raise AIError("No Anthropic API key. Add one in Settings or set ANTHROPIC_API_KEY.")
        return AnthropicProvider(api_key, settings.effective_model, settings.effort, log)
    if settings.provider == "openai":
        if not api_key:
            raise AIError("No OpenAI API key. Add one in Settings or set OPENAI_API_KEY.")
        return OpenAIProvider(api_key, settings.effective_model, "", log)
    if settings.provider == "openai_compatible":
        if not settings.base_url:
            raise AIError("Set the endpoint base URL for the OpenAI-compatible provider in Settings.")
        return OpenAIProvider(api_key, settings.effective_model, settings.base_url, log)
    raise AIError(f"Unknown provider '{settings.provider}'.")
