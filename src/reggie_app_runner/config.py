import json
import os
import re
import shlex
import socket
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dynaconf
import yaml
from reggie_app_runner import git


"""Dynaconf-backed configuration parsing for indexed app runner settings."""


DATABRICKS_APP_PORT = "DATABRICKS_APP_PORT"
RUNNER_PREFIX = "DATABRICKS_APP_RUNNER"
INDEXED_PREFIX_PATTERN = re.compile(rf"^{RUNNER_PREFIX}_(\d+)_")
RUNNER_TUNNEL_TOKEN = f"{RUNNER_PREFIX}_TUNNEL_TOKEN"
RUNNER_TUNNEL_REMOTE = f"{RUNNER_PREFIX}_TUNNEL_REMOTE"
LEGACY_TUNNEL_TOKEN = "DATABRICKS_APP_TUNNEL_TOKEN"
LEGACY_TUNNEL_REMOTE = "DATABRICKS_APP_TUNNEL_REMOTE"
DEFAULT_TUNNEL_REMOTE = "nectus.io"


@dataclass(frozen=True)
class AppConfig:
    index: int
    source: str
    route_path: str
    strip_path_prefix: bool
    env: dict[str, str]
    command: list[str]
    port: int
    git_token: str | None
    public_subdomain: str | None
    tunnel_token: str | None
    tunnel_remote: str
    update_interval_seconds: float | None
    redeploy_retry_seconds: float


@dataclass(frozen=True)
class RunnerConfig:
    apps: list[AppConfig]
    root_public_subdomain: str | None
    root_tunnel_token: str | None
    root_tunnel_remote: str


def read_app_configs() -> list[AppConfig]:
    default_settings = _new_settings(RUNNER_PREFIX)
    app_indexes = _read_indexes()
    configs: list[AppConfig] = []
    for index in app_indexes:
        app_settings = _new_settings(f"{RUNNER_PREFIX}_{index}")
        source = _read_str(app_settings, default_settings, "source")
        if not source:
            continue
        command = _to_command_list(_read_raw(app_settings, default_settings, "command"))
        configs.append(
            AppConfig(
                index=index,
                source=source,
                route_path=_normalize_path(
                    _read_str(app_settings, default_settings, "path")
                    or _default_route_path(source, index),
                ),
                strip_path_prefix=_read_bool(
                    app_settings,
                    default_settings,
                    "strip_path_prefix",
                    default=True,
                ),
                env=_to_env_map(_read_raw(app_settings, default_settings, "env")),
                command=command,
                port=_random_port(),
                git_token=_read_str(app_settings, default_settings, "github_token"),
                public_subdomain=_read_local_str(app_settings, "public_subdomain"),
                tunnel_token=_resolve_tunnel_token(app_settings, default_settings),
                tunnel_remote=_resolve_tunnel_remote(app_settings, default_settings),
                update_interval_seconds=_resolve_update_interval_seconds(
                    app_settings,
                    default_settings,
                ),
                redeploy_retry_seconds=_resolve_redeploy_retry_seconds(
                    app_settings,
                    default_settings,
                ),
            )
        )
    return configs


def read_runner_config() -> RunnerConfig:
    default_settings = _new_settings(RUNNER_PREFIX)
    return RunnerConfig(
        apps=read_app_configs(),
        root_public_subdomain=_read_str(default_settings, default_settings, "public_subdomain"),
        root_tunnel_token=_resolve_tunnel_token(default_settings, default_settings),
        root_tunnel_remote=_resolve_tunnel_remote(default_settings, default_settings),
    )


def caddy_listen_port() -> int:
    raw_port = os.environ.get(DATABRICKS_APP_PORT)
    if not raw_port:
        return 8000
    return int(raw_port)


def build_process_env(app_config: AppConfig) -> dict[str, str]:
    process_env: dict[str, str] = {}
    for name, value in os.environ.items():
        if not name.startswith(RUNNER_PREFIX):
            process_env[name] = value
    process_env.pop("VIRTUAL_ENV", None)
    process_env["PATH"] = _sanitized_path(process_env.get("PATH", ""))
    process_env[DATABRICKS_APP_PORT] = str(app_config.port)
    process_env.update(app_config.env)
    if os.path.isdir("/home/app"):
        process_env["HOME"] = "/home/app"
    else:
        process_env["HOME"] = os.environ.get("HOME", process_env.get("HOME", ""))
    return process_env


def _new_settings(prefix: str) -> dynaconf.Dynaconf:
    return dynaconf.Dynaconf(
        envvar_prefix=prefix,
        merge_enabled=True,
        load_dotenv=False,
        environments=False,
        settings_files=[],
    )


def _read_indexes() -> list[int]:
    indexes = set()
    for env_name in os.environ:
        if match := INDEXED_PREFIX_PATTERN.match(env_name):
            indexes.add(int(match.group(1)))
    return sorted(indexes)


def _read_raw(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
    name: str,
) -> Any:
    app_value = app_settings.get(name, None)
    if app_value is not None:
        return app_value
    return default_settings.get(name, None)


def _read_str(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
    name: str,
) -> str | None:
    value = _read_raw(app_settings, default_settings, name)
    if value is None:
        return None
    value_str = str(value).strip()
    return value_str or None


def _read_local_str(
    app_settings: dynaconf.Dynaconf,
    name: str,
) -> str | None:
    value = app_settings.get(name, None)
    if value is None:
        return None
    value_str = str(value).strip()
    return value_str or None


def _read_bool(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
    name: str,
    default: bool,
) -> bool:
    value = _read_raw(app_settings, default_settings, name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _normalize_path(path_value: str) -> str:
    path_value = path_value.strip() or "/"
    if not path_value.startswith("/"):
        path_value = f"/{path_value}"
    if path_value != "/":
        path_value = path_value.rstrip("/")
    return path_value


def _to_command_list(command_value: Any) -> list[str]:
    if command_value is None:
        return []
    if isinstance(command_value, (list, tuple)):
        return [str(item) for item in command_value if str(item).strip()]
    text = str(command_value).strip()
    if not text:
        return []
    if "," in text:
        split_by_comma = [part.strip() for part in text.split(",") if part.strip()]
        if len(split_by_comma) > 1:
            return split_by_comma
    return shlex.split(text)


def _to_env_map(env_value: Any) -> dict[str, str]:
    if env_value is None:
        return {}
    if isinstance(env_value, dict):
        return {str(key): str(value) for key, value in env_value.items()}
    if isinstance(env_value, str):
        text = env_value.strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            try:
                loaded = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ValueError(
                    "ENV must be a JSON/YAML object when provided as string"
                ) from exc
        if not isinstance(loaded, dict):
            raise ValueError("ENV string must decode to an object")
        return {str(key): str(value) for key, value in loaded.items()}
    raise ValueError("ENV must be a mapping or JSON/YAML object string")


def _random_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def _default_route_path(source: str, index: int) -> str:
    repo_name = _source_repo_name(source)
    if repo_name:
        return f"/{repo_name}"
    return f"/app_{index}"


def _source_repo_name(source: str) -> str | None:
    normalized = git.source_url(source)
    parsed = urlparse(normalized)
    path = parsed.path
    if not path and ":" in normalized and "/" in normalized:
        path = normalized.split(":", 1)[1]
    path = path.rstrip("/")
    if not path:
        return None
    name = path.split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or None


def _resolve_tunnel_token(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
) -> str | None:
    for key in ("tunnel_token",):
        value = _read_str(app_settings, default_settings, key)
        if value:
            return value
    for env_name in (RUNNER_TUNNEL_TOKEN, LEGACY_TUNNEL_TOKEN):
        if value := os.environ.get(env_name):
            text = value.strip()
            if text:
                return text
    return None


def _resolve_tunnel_remote(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
) -> str:
    for key in ("tunnel_remote",):
        value = _read_str(app_settings, default_settings, key)
        if value:
            return value
    for env_name in (RUNNER_TUNNEL_REMOTE, LEGACY_TUNNEL_REMOTE):
        if value := os.environ.get(env_name):
            text = value.strip()
            if text:
                return text
    return DEFAULT_TUNNEL_REMOTE


def _sanitized_path(path_value: str) -> str:
    if not path_value:
        return path_value
    runner_venv_bin = str((Path.cwd() / ".venv" / "bin").resolve())
    filtered_segments: list[str] = []
    for segment in path_value.split(os.pathsep):
        segment_value = segment.strip()
        if not segment_value:
            continue
        if segment_value == runner_venv_bin:
            continue
        filtered_segments.append(segment_value)
    return os.pathsep.join(filtered_segments)


def _resolve_update_interval_seconds(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
) -> float | None:
    for key in ("update_interval_seconds", "update_interval"):
        value = _read_raw(app_settings, default_settings, key)
        if value is None:
            continue
        return _to_optional_interval_seconds(value)
    return 30.0


def _resolve_redeploy_retry_seconds(
    app_settings: dynaconf.Dynaconf,
    default_settings: dynaconf.Dynaconf,
) -> float:
    for key in ("redeploy_retry_seconds", "deploy_retry_seconds", "retry_interval_seconds"):
        value = _read_raw(app_settings, default_settings, key)
        if value is None:
            continue
        seconds = _to_positive_seconds(value, default=5.0)
        return seconds
    return 5.0


def _to_optional_interval_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None if not value else 30.0
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "0", "false", "none", "off", "no", "null"}:
        return None
    seconds = float(text)
    if seconds <= 0:
        return None
    return seconds


def _to_positive_seconds(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"", "false", "none", "off", "no", "null"}:
        return default
    seconds = float(text)
    if seconds <= 0:
        return default
    return seconds
