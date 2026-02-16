"""Prompt profile abstraction for runtime orchestration."""

from __future__ import annotations

from longrun_agent.article.prompts import get_coding_prompt, get_initializer_prompt
from longrun_agent.feature_list import Feature
from longrun_agent.prompts import build_coding_prompt, build_initializer_prompt
from longrun_agent.runtime.context_guidance import (
    build_instruction_and_layered_reading_guidance,
)


class PromptProvider:
    """Select prompt source based on configured profile."""

    def __init__(self, profile: str = "default", backend_name: str = "codex_cli"):
        self.profile = profile
        if profile not in {"default", "article"}:
            raise ValueError(f"Unsupported prompt profile: {profile}")
        self.backend_name = backend_name

    def build_initializer_prompt(self, app_spec: str, feature_target: int) -> str:
        if self.profile == "article":
            prompt = get_initializer_prompt()
        else:
            prompt = build_initializer_prompt(app_spec=app_spec, feature_target=feature_target)
        return self._with_context_guidance(prompt)

    def build_coding_prompt(
        self,
        app_spec: str,
        feature_index: int,
        feature: Feature,
        passing: int,
        total: int,
    ) -> str:
        if self.profile == "article":
            prompt = get_coding_prompt()
        else:
            prompt = build_coding_prompt(
                app_spec=app_spec,
                feature_index=feature_index,
                feature=feature,
                passing=passing,
                total=total,
            )
        return self._with_context_guidance(prompt)

    def _with_context_guidance(self, prompt: str) -> str:
        guidance = build_instruction_and_layered_reading_guidance(self.backend_name)
        return f"{prompt}\n\n{guidance}"
