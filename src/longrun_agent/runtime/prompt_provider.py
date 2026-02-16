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

    def __init__(
        self,
        profile: str = "default",
        backend_name: str = "codex_cli",
        *,
        app_spec_path: str = "app_spec.txt",
        feature_list_path: str = "feature_list.json",
        progress_path: str = "claude-progress.txt",
        init_script_path: str = "init.sh",
    ):
        self.profile = profile
        if profile not in {"default", "article"}:
            raise ValueError(f"Unsupported prompt profile: {profile}")
        self.backend_name = backend_name
        self.app_spec_path = app_spec_path
        self.feature_list_path = feature_list_path
        self.progress_path = progress_path
        self.init_script_path = init_script_path

    def build_initializer_prompt(self, app_spec: str, feature_target: int) -> str:
        if self.profile == "article":
            prompt = get_initializer_prompt()
        else:
            prompt = build_initializer_prompt(
                app_spec=app_spec,
                feature_target=feature_target,
                app_spec_path=self.app_spec_path,
                feature_list_path=self.feature_list_path,
                progress_path=self.progress_path,
                init_script_path=self.init_script_path,
            )
        return self._with_context_guidance(self._remap_artifact_paths(prompt))

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
                app_spec_path=self.app_spec_path,
                feature_list_path=self.feature_list_path,
                progress_path=self.progress_path,
            )
        return self._with_context_guidance(self._remap_artifact_paths(prompt))

    def _remap_artifact_paths(self, prompt: str) -> str:
        replacements = {
            "app_spec.txt": self.app_spec_path,
            "feature_list.json": self.feature_list_path,
            "claude-progress.txt": self.progress_path,
            "init.sh": self.init_script_path,
        }
        remapped = prompt
        for source, target in replacements.items():
            remapped = remapped.replace(source, target)
        return remapped

    def _with_context_guidance(self, prompt: str) -> str:
        guidance = build_instruction_and_layered_reading_guidance(self.backend_name)
        artifact_paths = (
            "### Artifact file paths (authoritative)\n"
            f"- App spec: `{self.app_spec_path}`\n"
            f"- Feature list: `{self.feature_list_path}`\n"
            f"- Progress log: `{self.progress_path}`\n"
            f"- Init script: `{self.init_script_path}`\n"
            "- Use these exact paths even if other instructions mention root-level filenames."
        )
        return f"{prompt}\n\n{artifact_paths}\n\n{guidance}"
