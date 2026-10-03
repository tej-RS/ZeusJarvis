"""Conversation session behind J.A.R.V.I.S. OS.

Builds the same agent stack as ``jarvis chat`` (engine, security guardrails,
tools, persona prompt, long-term memory) in observable steps so the boot
screen can report each one, then answers messages with multi-turn history.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

SESSION_CONTEXT = (
    "Interface: J.A.R.V.I.S. OS, a full-screen terminal HUD on the user's Mac. "
    "Replies appear in a terminal panel, so keep them concise; short markdown is "
    "fine. You may read files anywhere on this Mac. Running commands and "
    "writing or patching files show the user an Allow/Deny prompt first, so "
    "use those tools when they help instead of asking for permission in chat. "
    "Control the Mac's apps with the mac_apps, mac_media and mac_system "
    "tools, and applescript for anything they don't cover. The user can run "
    "shell commands themselves by typing !<command>, open apps with "
    "/open <name>, and talk by voice with Ctrl+T."
)

# J.A.R.V.I.S. OS offers these on top of the configured tools. They change
# files, so like shell_exec every call asks first; that is why they are added
# here rather than in config, where `jarvis ask` and the API run them unasked.
HUD_EXTRA_TOOLS = ("file_write", "apply_patch")
CONFIRM_FIRST = frozenset(HUD_EXTRA_TOOLS)


def _confirming(tool_cls: type) -> type:
    """Subclass of ``tool_cls`` whose spec requires the user's confirmation."""

    class ConfirmFirst(tool_cls):  # type: ignore[misc, valid-type]
        @property
        def spec(self) -> Any:
            return replace(super().spec, requires_confirmation=True)

    ConfirmFirst.__name__ = ConfirmFirst.__qualname__ = tool_cls.__name__
    return ConfirmFirst


@dataclass(frozen=True)
class BootStep:
    """Result of one start-up check, shown on the boot screen."""

    label: str
    status: str  # "ok", "warn" or "fail"
    detail: str = ""


class JarvisSession:
    """One JARVIS conversation: agent, history and memory.

    ``confirm`` is called (from a worker thread) when a tool needs the user's
    permission and must return their answer. ``on_tool_event`` receives
    ``("start" | "end", payload)`` as tools run.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        confirm: Optional[Callable[[str], bool]] = None,
        on_tool_event: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._requested_model = model
        self._confirm = confirm
        self._on_tool_event = on_tool_event
        self._lock = threading.Lock()
        self.config: Any = None
        self.bus: Any = None
        self.engine: Any = None
        self.engine_name = ""
        self.model = ""
        self.agent: Any = None
        self.agent_name = ""
        self.tool_names: List[str] = []
        self.history: List[Any] = []
        self._security: Any = None
        self._tools: List[Any] = []
        self._memory_service: Any = None
        self._memory_backend: Any = None

    @property
    def online(self) -> bool:
        return self.engine is not None and bool(self.model)

    # ------------------------------------------------------------------ boot

    def boot(self, report: Callable[[BootStep], None]) -> bool:
        """Initialise everything, reporting each step. True if chat works."""
        from openjarvis.core.config import load_config
        from openjarvis.core.events import EventBus, EventType

        self.config = load_config()
        self.bus = EventBus(record_history=False)
        self.bus.subscribe(EventType.TOOL_CALL_START, self._tool_started)
        self.bus.subscribe(EventType.TOOL_CALL_END, self._tool_finished)
        report(BootStep("Configuration", "ok", "~/.openjarvis/config.toml"))

        if not self._connect_engine(report):
            return False

        from openjarvis.security import setup_security

        self._security = setup_security(self.config, self.engine, self.bus)
        self.engine = self._security.engine
        profile = getattr(self.config.security, "profile", "") or "default"
        report(BootStep("Security protocols", "ok", f"{profile} profile"))

        self._tools = self._build_tools()
        self.tool_names = [t.spec.name for t in self._tools]
        if self._tools:
            report(BootStep("Tool systems", "ok", f"{len(self._tools)} armed"))
        else:
            report(BootStep("Tool systems", "warn", "none configured"))

        try:
            self._build_agent()
            report(BootStep("Agent", "ok", self.agent_name or "direct"))
        except Exception as exc:  # fall back to plain chat with the engine
            logger.debug("Agent construction failed", exc_info=True)
            self.agent = None
            report(BootStep("Agent", "warn", f"direct mode ({exc})"))

        soul = Path(self.config.memory_files.soul_path).expanduser()
        if soul.exists():
            report(BootStep("Persona", "ok", "SOUL.md · USER.md · MEMORY.md"))
        else:
            report(BootStep("Persona", "warn", "default persona"))

        report(self._start_memory())
        report(self._voice_status())
        report(self._network_status())
        return True

    def _connect_engine(self, report: Callable[[BootStep], None]) -> bool:
        from openjarvis.engine import get_engine
        from openjarvis.intelligence import register_builtin_models

        register_builtin_models()
        engine_key = self.config.engine.default or None
        resolved = get_engine(self.config, engine_key)
        if resolved is None and engine_key == "ollama" and _start_ollama():
            report(BootStep("Neural core", "warn", "started ollama serve"))
            for _ in range(20):
                time.sleep(0.5)
                resolved = get_engine(self.config, engine_key)
                if resolved is not None:
                    break
        if resolved is None:
            report(
                BootStep("Neural core", "fail", f"{engine_key or 'engine'} unreachable")
            )
            return False
        self.engine_name, self.engine = resolved

        available = _safe_list_models(self.engine)
        wanted = (
            self._requested_model
            or getattr(self.config.intelligence, "model_chat", "")
            or self.config.intelligence.default_model
        )
        if wanted and (not available or wanted in available):
            self.model = wanted
        elif available:
            self.model = available[0]
            report(
                BootStep("Neural core", "warn", f"{wanted} missing; using {self.model}")
            )
        else:
            report(BootStep("Neural core", "fail", "no models installed"))
            return False
        report(BootStep("Neural core", "ok", f"{self.model} via {self.engine_name}"))
        return True

    def _build_tools(self) -> List[Any]:
        from openjarvis.cli._tool_names import resolve_tool_names

        names = resolve_tool_names(
            None,
            getattr(self.config.tools, "enabled", None),
            getattr(self.config.agent, "tools", None),
        )
        if not names:
            return []
        names = list(dict.fromkeys([*names, *HUD_EXTRA_TOOLS]))
        import openjarvis.tools  # noqa: F401 — trigger registration
        from openjarvis.core.registry import ToolRegistry
        from openjarvis.tools._stubs import BaseTool

        tools: List[Any] = []
        for name in names:
            if not ToolRegistry.contains(name):
                continue
            entry = ToolRegistry.get(name)
            if isinstance(entry, type) and issubclass(entry, BaseTool):
                tools.append((_confirming(entry) if name in CONFIRM_FIRST else entry)())
            elif isinstance(entry, BaseTool):
                tools.append(entry)
        return tools

    def _build_agent(self) -> None:
        """(Re)build the agent; also picks up persona file changes."""
        import inspect

        import openjarvis.agents  # noqa: F401 — trigger registration
        from openjarvis.core.registry import AgentRegistry
        from openjarvis.prompt.builder import SystemPromptBuilder
        from openjarvis.security.runtime import (
            agent_security_kwargs,
            wire_agent_security,
        )

        key = self.config.agent.default_agent
        self.agent = None
        self.agent_name = ""
        if not key or key == "none" or not AgentRegistry.contains(key):
            return
        agent_cls = AgentRegistry.get(key)
        kwargs: dict = {"bus": self.bus}
        if getattr(agent_cls, "accepts_tools", False):
            if self._tools:
                kwargs["tools"] = self._tools
            kwargs["max_turns"] = self.config.agent.max_turns
            kwargs["interactive"] = self._confirm is not None
            kwargs["confirm_callback"] = self._confirm
        kwargs.update(
            agent_security_kwargs(
                agent_cls,
                capability_policy=self._security.capability_policy,
                rate_limiter=self._security.rate_limiter,
                agent_id=key,
            )
        )
        if "prompt_builder" in inspect.signature(agent_cls.__init__).parameters:
            kwargs["prompt_builder"] = SystemPromptBuilder(
                agent_template=self.config.agent.default_system_prompt or "",
                memory_files_config=self.config.memory_files,
                system_prompt_config=self.config.system_prompt,
                session_context=SESSION_CONTEXT,
            )
        self.agent = agent_cls(self.engine, self.model, **kwargs)
        wire_agent_security(
            self.agent,
            bus=self.bus,
            capability_policy=self._security.capability_policy,
            rate_limiter=self._security.rate_limiter,
            agent_id=key,
        )
        self.agent_name = key

    def _start_memory(self) -> BootStep:
        from openjarvis._rust_bridge import RUST_AVAILABLE

        try:
            from openjarvis.memory import build_memory_service

            self._memory_service = build_memory_service(
                self.config, self.engine, self.model, event_bus=self.bus
            )
            if self._memory_service is not None:
                self._memory_service.start()
            if self.config.agent.context_from_memory:
                from openjarvis.cli.ask import _get_memory_backend

                self._memory_backend = _get_memory_backend(self.config)
        except Exception as exc:
            logger.debug("Memory service unavailable", exc_info=True)
            return BootStep("Memory matrix", "warn", f"unavailable ({exc})")
        if not RUST_AVAILABLE:
            return BootStep("Memory matrix", "warn", "online (no Rust extension)")
        return BootStep("Memory matrix", "ok", "online")

    def _voice_status(self) -> BootStep:
        speech = getattr(self.config, "speech", None)
        has_tts = importlib.util.find_spec("kokoro") is not None
        has_stt = importlib.util.find_spec("faster_whisper") is not None
        if has_tts and has_stt:
            voice = getattr(speech, "voice_id", "") or "default"
            return BootStep("Voice systems", "ok", f"kokoro · {voice} · whisper")
        if has_tts or has_stt:
            return BootStep("Voice systems", "warn", "partial (uv sync --extra voice)")
        return BootStep("Voice systems", "warn", "offline (uv sync --extra voice)")

    @staticmethod
    def _network_status() -> BootStep:
        # getaddrinfo ignores socket timeouts, so bound it with a thread.
        resolved: List[bool] = []

        def probe() -> None:
            try:
                socket.getaddrinfo("duckduckgo.com", 443)
                resolved.append(True)
            except OSError:
                resolved.append(False)

        thread = threading.Thread(target=probe, daemon=True)
        thread.start()
        thread.join(timeout=2.5)
        if resolved and resolved[0]:
            return BootStep("Network uplink", "ok", "online")
        return BootStep("Network uplink", "warn", "offline (web search unavailable)")

    # ------------------------------------------------------------ messages

    def ask(
        self, text: str, should_discard: Optional[Callable[[], bool]] = None
    ) -> str:
        """Send one message (blocking) and return JARVIS's reply.

        If ``should_discard()`` is true once the reply arrives (the user
        cancelled), the exchange is dropped from history before the next
        message can start.
        """
        from openjarvis.core.types import Message, Role
        from openjarvis.memory import publish_completed_exchange

        if not self.online:
            raise RuntimeError("Neural core offline")
        with self._lock:
            self.history.append(Message(role=Role.USER, content=text))
            context_message = None
            generation_history = self.history
            if self.config.agent.context_from_memory:
                try:
                    context = self._memory_context(text)
                    if self.agent is not None:
                        context_message = context[0] if context else None
                    else:
                        generation_history = context
                except Exception:
                    logger.debug("Failed to inject memory context", exc_info=True)

            if self.agent is not None:
                from openjarvis.agents._stubs import AgentContext

                agent_context = AgentContext()
                if context_message is not None:
                    agent_context.conversation.add(context_message)
                for message in self.history[:-1]:
                    if message.role != Role.SYSTEM:
                        agent_context.conversation.add(message)
                response = self.agent.run(text, context=agent_context)
                content = getattr(response, "content", None) or str(response)
            else:
                result = self.engine.generate(generation_history, model=self.model)
                content = (
                    result.get("content", "")
                    if isinstance(result, dict)
                    else str(result)
                )

            if should_discard is not None and should_discard():
                self.history.pop()  # the user message; the reply was never shown
                return content
            self.history.append(Message(role=Role.ASSISTANT, content=content))
        try:
            publish_completed_exchange(self.bus, text, content, source="cli.os")
        except Exception:
            logger.debug("Failed to publish exchange", exc_info=True)
        return content

    def _memory_context(self, text: str) -> list:
        from openjarvis.memory import load_configured_facts
        from openjarvis.tools.storage.context import ContextConfig, inject_context

        if self._memory_service is not None and hasattr(
            self._memory_service, "list_facts"
        ):
            facts = self._memory_service.list_facts()
        else:
            facts = load_configured_facts(self.config)
        memory = self.config.memory
        return inject_context(
            text,
            [] if self.agent is not None else self.history,
            self._memory_backend,
            config=ContextConfig(
                top_k=memory.context_top_k,
                min_score=memory.context_min_score,
                max_context_tokens=memory.context_max_tokens,
            ),
            facts=facts,
        )

    def clear(self) -> None:
        with self._lock:
            self.history = []

    def list_models(self) -> List[str]:
        return _safe_list_models(self.engine) if self.engine is not None else []

    def set_model(self, model: str) -> None:
        if self.engine is None:
            raise RuntimeError("Neural core offline")
        self.model = model
        self._build_agent()

    def reload_persona(self) -> None:
        """Rebuild the agent so edits to USER.md/MEMORY.md take effect.

        The prompt builder reads the persona files once, when first built.
        """
        if self.engine is not None and self.config is not None:
            self._build_agent()

    def close(self) -> None:
        if self._memory_service is not None:
            try:
                self._memory_service.stop()
            except Exception:
                logger.debug("Memory service stop failed", exc_info=True)

    # ---------------------------------------------------------- tool events

    def _tool_started(self, event: Any) -> None:
        if self._on_tool_event is not None:
            self._on_tool_event("start", dict(getattr(event, "data", {}) or {}))

    def _tool_finished(self, event: Any) -> None:
        if self._on_tool_event is not None:
            self._on_tool_event("end", dict(getattr(event, "data", {}) or {}))


def _safe_list_models(engine: Any) -> List[str]:
    try:
        return list(engine.list_models() or [])
    except Exception:
        logger.debug("list_models failed", exc_info=True)
        return []


def _start_ollama() -> bool:
    """Launch ``ollama serve`` in the background if it is installed."""
    binary = shutil.which("ollama")
    if binary is None:
        return False
    log_dir = Path.home() / ".openjarvis" / ".state"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "ollama.log", "ab") as log:
        subprocess.Popen(
            [binary, "serve"],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return True


__all__ = ["BootStep", "JarvisSession", "SESSION_CONTEXT"]
