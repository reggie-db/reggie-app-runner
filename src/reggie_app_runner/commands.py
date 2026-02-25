import shlex
from pathlib import Path
from typing import Any

import yaml


"""Command resolution for app execution with app.yaml fallback."""


def resolve_command(source_dir: Path, explicit_command: list[str]) -> list[str]:
    command, _ = resolve_entrypoint(source_dir, explicit_command)
    return command


def resolve_entrypoint(source_dir: Path, explicit_command: list[str]) -> tuple[list[str], Path]:
    if explicit_command:
        return explicit_command, source_dir
    app_yaml = _resolve_app_yaml_path(source_dir)
    return read_command_from_app_yaml_file(app_yaml), app_yaml.parent


def read_command_from_app_yaml(source_dir: Path) -> list[str]:
    app_yaml = _resolve_app_yaml_path(source_dir)
    return read_command_from_app_yaml_file(app_yaml)


def read_command_from_app_yaml_file(app_yaml: Path) -> list[str]:
    payload = yaml.safe_load(app_yaml.read_text()) or {}
    command_value = payload.get("command")
    if command_value is None:
        raise ValueError(f"app.yaml has no command for {app_yaml.parent}")
    return _normalize_command(command_value)


def _normalize_command(command_value: Any) -> list[str]:
    if isinstance(command_value, list):
        normalized = [str(item).strip() for item in command_value if str(item).strip()]
        if not normalized:
            raise ValueError("command list must not be empty")
        return normalized
    if isinstance(command_value, str):
        normalized = shlex.split(command_value)
        if not normalized:
            raise ValueError("command string must not be empty")
        return normalized
    raise ValueError("command must be a list or string")


def _resolve_app_yaml_path(source_dir: Path) -> Path:
    root_app_yaml = source_dir / "app.yaml"
    if root_app_yaml.is_file():
        return root_app_yaml

    nested_app_yamls = [
        path for path in source_dir.rglob("app.yaml") if ".git" not in path.parts
    ]
    if len(nested_app_yamls) == 1:
        return nested_app_yamls[0]
    if not nested_app_yamls:
        raise ValueError(f"Missing app.yaml and no COMMAND override for {source_dir}")
    discovered = ", ".join(str(path.relative_to(source_dir)) for path in nested_app_yamls[:5])
    raise ValueError(
        f"Multiple app.yaml files found ({len(nested_app_yamls)}) in {source_dir}: {discovered}"
    )
