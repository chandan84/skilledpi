"""PiAgent — the core Chibu agent backed by Anthropic Claude."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from chibu.agent.skill_loader import Skill, load_skills
from chibu.extension.runner import ExtensionRunner
from chibu.utils.filesystem import get_default_model, load_config, load_models

logger = logging.getLogger("chibu.agent")


@dataclass
class AgentEvent:
    event_type: str  # "text" | "tool_use" | "tool_result" | "error" | "done"
    content: str = ""
    tool_name: str = ""
    is_done: bool = False
    metadata: dict = field(default_factory=dict)


class PiAgent:
    """A single Chibu Pi agent instance."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        agent_group: str,
        auth_token: str,
        root: Path,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.agent_group = agent_group
        self.auth_token = auth_token
        self.root = root
        self.pi_dir = root / ".pi"

        self._config = load_config(root)
        self._models = load_models(root)
        self._default_model = get_default_model(self._models)
        self._skills: dict[str, Skill] = {}
        self._extension = ExtensionRunner(root, agent_id, name)

        # Anthropic client — reads ANTHROPIC_API_KEY from env
        self._llm = anthropic.AsyncAnthropic()
        self._status = "idle"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load skills from .pi/skills/."""
        self._skills = load_skills(self.pi_dir)
        logger.info(
            "Agent %s loaded %d skill(s): %s",
            self.name,
            len(self._skills),
            list(self._skills.keys()),
        )
        self._status = "ready"

    def verify_token(self, token: str) -> bool:
        return token == self.auth_token

    # ── execution ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        prompt: str,
        session_id: str = "",
        model_id: str = "",
        context: dict | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a prompt and yield streaming events."""
        if not session_id:
            session_id = str(uuid.uuid4())

        model = model_id or self._default_model or "claude-sonnet-4-6"
        max_turns = self._config.get("agent", {}).get("max_turns", 20)
        self._status = "running"

        messages: list[dict] = []
        if context:
            sys_extra = "\n".join(f"{k}: {v}" for k, v in context.items())
            messages.append({"role": "user", "content": f"Context:\n{sys_extra}"})
            messages.append({"role": "assistant", "content": "Understood."})

        messages.append({"role": "user", "content": prompt})
        tools = [s.to_anthropic_tool() for s in self._skills.values()]

        try:
            for _turn in range(max_turns):
                ext_ctx = self._extension.before_action(
                    "llm_request",
                    {
                        "model": model,
                        "prompt_tokens": len(prompt) // 4,
                        "turn": _turn,
                    },
                    session_id,
                )

                text_buf = ""
                tool_calls: list[dict] = []

                try:
                    async with self._llm.messages.stream(
                        model=model,
                        max_tokens=self._config.get("agent", {}).get(
                            "max_tokens", 4096
                        ),
                        messages=messages,
                        tools=tools if tools else anthropic.NOT_GIVEN,
                        system=(
                            f"You are {self.name}, a Chibu Pi agent from the badmono org. "
                            "Use the available skills (tools) to accomplish the user's task. "
                            "Be concise and accurate."
                        ),
                    ) as stream:
                        async for event in stream:
                            if event.type == "content_block_delta":
                                if hasattr(event.delta, "text"):
                                    text_buf += event.delta.text
                                    yield AgentEvent(
                                        event_type="text",
                                        content=event.delta.text,
                                    )
                            elif event.type == "content_block_start":
                                if (
                                    hasattr(event.content_block, "type")
                                    and event.content_block.type == "tool_use"
                                ):
                                    tool_calls.append(
                                        {
                                            "id": event.content_block.id,
                                            "name": event.content_block.name,
                                            "input_buf": "",
                                        }
                                    )
                            elif event.type == "content_block_stop":
                                pass  # handled below via final_message

                        final_msg = await stream.get_final_message()

                    self._extension.after_action(
                        ext_ctx,
                        result={
                            "completion_tokens": final_msg.usage.output_tokens,
                            "stop_reason": final_msg.stop_reason,
                        },
                    )

                except anthropic.APIError as exc:
                    self._extension.after_action(ext_ctx, error=exc)
                    yield AgentEvent(event_type="error", content=str(exc), is_done=True)
                    return

                # Build tool_calls from final message content
                tool_use_blocks = [
                    b for b in final_msg.content if b.type == "tool_use"
                ]

                if not tool_use_blocks:
                    # No more tool calls — done
                    break

                # Execute tool calls
                assistant_content = [b.model_dump() for b in final_msg.content]
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = []
                for tb in tool_use_blocks:
                    yield AgentEvent(
                        event_type="tool_use",
                        content=json.dumps(tb.input),
                        tool_name=tb.name,
                    )

                    skill_ctx = self._extension.before_action(
                        "skill_execute",
                        {"skill": tb.name, "input": tb.input},
                        session_id,
                    )
                    try:
                        skill = self._skills.get(tb.name)
                        if skill:
                            result_val = await skill.execute(**tb.input)
                        else:
                            result_val = f"Error: skill '{tb.name}' not found"

                        result_str = (
                            json.dumps(result_val)
                            if not isinstance(result_val, str)
                            else result_val
                        )
                        self._extension.after_action(
                            skill_ctx, result={"output": result_str}
                        )
                    except Exception as exc:  # noqa: BLE001
                        result_str = f"Skill error: {exc}"
                        self._extension.after_action(skill_ctx, error=exc)

                    yield AgentEvent(
                        event_type="tool_result",
                        content=result_str,
                        tool_name=tb.name,
                    )

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": result_str,
                        }
                    )

                messages.append({"role": "user", "content": tool_results})

        finally:
            self._status = "idle"

        yield AgentEvent(event_type="done", is_done=True)

    # ── info ──────────────────────────────────────────────────────────────────

    def info(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "agent_group": self.agent_group,
            "version": self._config.get("agent", {}).get("version", "0.1.0"),
            "skills": list(self._skills.keys()),
            "models": [m["id"] for m in self._models],
            "status": self._status,
        }
