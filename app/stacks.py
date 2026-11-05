from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import settings
from app.i18n import tr
from app.llm.openai_client import OpenAILLMClient, is_local_endpoint

MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,120}$")


@dataclass(frozen=True)
class Stack:
    """One complete way of running the model: everything that changes when you swap it."""

    id: str
    name: str
    base_url: str
    model: str
    api_key: str = ""
    reasoning_effort: str = ""
    max_output_tokens: int = 2500
    timeout_seconds: int = 60
    builtin: bool = False


def builtin_stacks() -> list[Stack]:
    local = settings.local_llm_url
    groq = "https://api.groq.com/openai/v1"
    return [
        Stack("local-gemma4-26b", tr("stack.local", model="gemma4:26b"), local, "gemma4:26b", "ollama", "none", 6000, 900, True),
        Stack("local-gemma4-12b", tr("stack.local", model="gemma4:12b"), local, "gemma4:12b", "ollama", "none", 6000, 900, True),
        Stack("groq-gpt-oss-120b", tr("stack.groq", model="gpt-oss-120b"), groq, "openai/gpt-oss-120b", "", "", 2500, 60, True),
    ]


def _custom_path() -> Path:
    return Path(settings.stacks_path)


def custom_stacks() -> list[Stack]:
    path = _custom_path()
    if not path.exists():
        return []
    return [Stack(**entry) for entry in json.loads(path.read_text(encoding="utf-8"))]


def all_stacks() -> list[Stack]:
    return [*builtin_stacks(), *custom_stacks()]


def find_stack(stack_id: str) -> Stack | None:
    return next((s for s in all_stacks() if s.id == stack_id), None)


def current_stack() -> Stack:
    """What the bot is configured to use right now."""
    return Stack(
        id="current",
        name=tr("stack.current"),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        reasoning_effort=settings.llm_reasoning_effort,
        max_output_tokens=settings.llm_max_output_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _write_custom(stacks: list[Stack]) -> None:
    path = _custom_path()
    path.write_text(json.dumps([asdict(s) for s in stacks], ensure_ascii=False, indent=1), encoding="utf-8")
    path.chmod(0o600)  # may hold API keys


def save_custom(stack: Stack) -> None:
    _write_custom([*[s for s in custom_stacks() if s.id != stack.id], stack])


def delete_custom(stack_id: str) -> bool:
    remaining = [s for s in custom_stacks() if s.id != stack_id]
    if len(remaining) == len(custom_stacks()):
        return False
    _write_custom(remaining)
    return True


def is_external(stack: Stack) -> bool:
    return not is_local_endpoint(stack.base_url)


def make_client(stack: Stack, allow_external: bool = False) -> OpenAILLMClient:
    """A client of its own, so trying a stack never touches what the bot is running."""
    return OpenAILLMClient(
        api_key=stack.api_key or settings.openai_api_key,
        model=stack.model,
        base_url=stack.base_url or None,
        timeout=stack.timeout_seconds,
        max_output_tokens=stack.max_output_tokens,
        reasoning_effort=stack.reasoning_effort,
        allow_external=allow_external,
    )


def env_changes(stack: Stack, external_allowed: bool) -> dict[str, str]:
    """The .env values that make this stack the active one."""
    changes = {
        "LLM_PROVIDER": "openai",
        "OPENAI_BASE_URL": stack.base_url,
        "OPENAI_MODEL": stack.model,
        "LLM_REASONING_EFFORT": stack.reasoning_effort,
        "LLM_MAX_OUTPUT_TOKENS": str(stack.max_output_tokens),
        "LLM_TIMEOUT_SECONDS": str(stack.timeout_seconds),
        "LLM_EXTERNAL_ALLOWED": "true" if external_allowed else "false",
    }
    if stack.api_key:
        changes["OPENAI_API_KEY"] = stack.api_key
    return changes
