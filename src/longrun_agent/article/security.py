"""Bash command validation hook for article-mode harness."""

from __future__ import annotations

import os
import re
import shlex
from typing import Any


ALLOWED_COMMANDS = {
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "cp",
    "mkdir",
    "chmod",
    "pwd",
    "npm",
    "node",
    "git",
    "ps",
    "lsof",
    "sleep",
    "pkill",
    "init.sh",
}

COMMANDS_NEEDING_EXTRA_VALIDATION = {"pkill", "chmod", "init.sh"}


def split_command_segments(command_string: str) -> list[str]:
    segments = re.split(r"\s*(?:&&|\|\|)\s*", command_string)
    output: list[str] = []
    for segment in segments:
        parts = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', segment)
        for part in parts:
            part = part.strip()
            if part:
                output.append(part)
    return output


def extract_commands(command_string: str) -> list[str]:
    commands: list[str] = []
    segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', command_string)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        try:
            tokens = shlex.split(segment)
        except ValueError:
            return []

        expect_command = True
        for token in tokens:
            if token in ("|", "||", "&&", "&"):
                expect_command = True
                continue
            if token in {
                "if",
                "then",
                "else",
                "elif",
                "fi",
                "for",
                "while",
                "until",
                "do",
                "done",
                "case",
                "esac",
                "in",
                "!",
                "{",
                "}",
            }:
                continue
            if token.startswith("-"):
                continue
            if "=" in token and not token.startswith("="):
                continue

            if expect_command:
                commands.append(os.path.basename(token))
                expect_command = False

    return commands


def validate_pkill_command(command_string: str) -> tuple[bool, str]:
    allowed_process_names = {"node", "npm", "npx", "vite", "next"}

    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse pkill command"

    args = [token for token in tokens[1:] if not token.startswith("-")]
    if not args:
        return False, "pkill requires a process name"

    target = args[-1]
    if " " in target:
        target = target.split()[0]

    if target in allowed_process_names:
        return True, ""
    return False, f"pkill only allowed for dev processes: {allowed_process_names}"


def validate_chmod_command(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse chmod command"

    if not tokens or tokens[0] != "chmod":
        return False, "Not a chmod command"

    mode = None
    files: list[str] = []
    for token in tokens[1:]:
        if token.startswith("-"):
            return False, "chmod flags are not allowed"
        if mode is None:
            mode = token
        else:
            files.append(token)

    if mode is None:
        return False, "chmod requires a mode"
    if not files:
        return False, "chmod requires at least one file"

    if not re.match(r"^[ugoa]*\+x$", mode):
        return False, f"chmod only allowed with +x mode, got: {mode}"

    return True, ""


def validate_init_script(command_string: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse init script command"

    if not tokens:
        return False, "Empty command"

    script = tokens[0]
    if script == "./init.sh" or script.endswith("/init.sh"):
        return True, ""

    return False, f"Only ./init.sh is allowed, got: {script}"


def get_command_for_validation(cmd: str, segments: list[str]) -> str:
    for segment in segments:
        if cmd in extract_commands(segment):
            return segment
    return ""


async def bash_security_hook(
    input_data: dict[str, Any], tool_use_id: str | None = None, context: Any = None
) -> dict[str, str]:
    del tool_use_id, context

    if input_data.get("tool_name") != "Bash":
        return {}

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        return {}

    commands = extract_commands(command)
    if not commands:
        return {
            "decision": "block",
            "reason": f"Could not parse command for security validation: {command}",
        }

    segments = split_command_segments(command)

    for cmd in commands:
        if cmd not in ALLOWED_COMMANDS:
            return {
                "decision": "block",
                "reason": f"Command '{cmd}' is not in the allowed commands list",
            }

        if cmd in COMMANDS_NEEDING_EXTRA_VALIDATION:
            segment = get_command_for_validation(cmd, segments) or command
            if cmd == "pkill":
                ok, reason = validate_pkill_command(segment)
            elif cmd == "chmod":
                ok, reason = validate_chmod_command(segment)
            else:
                ok, reason = validate_init_script(segment)

            if not ok:
                return {"decision": "block", "reason": reason}

    return {}
