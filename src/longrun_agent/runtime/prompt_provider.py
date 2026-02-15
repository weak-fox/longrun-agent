"""Prompt profile abstraction for runtime orchestration."""

from __future__ import annotations

from longrun_agent.article.prompts import get_coding_prompt, get_initializer_prompt
from longrun_agent.feature_list import Feature
from longrun_agent.prompts import build_coding_prompt, build_initializer_prompt


class PromptProvider:
    """Select prompt source based on configured profile."""

    def __init__(self, profile: str = "default"):
        self.profile = profile
        if profile not in {"default", "article"}:
            raise ValueError(f"Unsupported prompt profile: {profile}")

    def build_initializer_prompt(self, app_spec: str, feature_target: int) -> str:
        if self.profile == "article":
            return get_initializer_prompt()
        return build_initializer_prompt(app_spec=app_spec, feature_target=feature_target)

    def build_coding_prompt(
        self,
        app_spec: str,
        feature_index: int,
        feature: Feature,
        passing: int,
        total: int,
    ) -> str:
        if self.profile == "article":
            return get_coding_prompt()
        return build_coding_prompt(
            app_spec=app_spec,
            feature_index=feature_index,
            feature=feature,
            passing=passing,
            total=total,
        )

