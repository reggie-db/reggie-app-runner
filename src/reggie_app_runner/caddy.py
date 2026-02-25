import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reggie_app_runner.config import AppConfig


"""Caddy configuration generation and process helpers."""


@dataclass(frozen=True)
class ProxyRoute:
    path: str
    port: int
    strip_path_prefix: bool = True


def build_config(
    listen_port: int,
    apps: list[AppConfig],
    extra_routes: list[ProxyRoute] | None = None,
    fallback_route: ProxyRoute | None = None,
) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    proxy_routes = [
        ProxyRoute(
            path=app.route_path,
            port=app.port,
            strip_path_prefix=app.strip_path_prefix,
        )
        for app in apps
    ]
    if extra_routes:
        proxy_routes.extend(extra_routes)

    for route_item in sorted(proxy_routes, key=lambda item: len(item.path), reverse=True):
        route: dict[str, Any] = {
            "match": [{"path": [f"{route_item.path}*"]}],
            "handle": _route_handlers(route_item),
        }
        routes.append(route)
    if fallback_route is not None:
        routes.append(_fallback_route(fallback_route))

    return {
        "admin": {"disabled": True},
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "listen": [f":{listen_port}"],
                        "routes": routes,
                    }
                }
            }
        },
    }


def start_caddy(config_payload: dict[str, Any]) -> tuple[subprocess.Popen, Path]:
    config_file = _write_temp_config(config_payload)
    process = subprocess.Popen(
        [_caddy_binary(), "run", "--config", str(config_file)],
        start_new_session=True,
    )
    return process, config_file


def stop_caddy(process: subprocess.Popen | None, config_file: Path | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if config_file is not None and config_file.exists():
        config_file.unlink()


def _route_handlers(route_item: ProxyRoute) -> list[dict[str, Any]]:
    handlers: list[dict[str, Any]] = []
    if route_item.strip_path_prefix and route_item.path != "/":
        handlers.append(
            {
                "handler": "rewrite",
                "strip_path_prefix": route_item.path,
            }
        )
    handlers.append(
        {
            "handler": "reverse_proxy",
            "upstreams": [{"dial": f"127.0.0.1:{route_item.port}"}],
        }
    )
    return handlers


def _fallback_route(route_item: ProxyRoute) -> dict[str, Any]:
    if route_item.path == "/":
        return {
            "handle": [
                {
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": f"127.0.0.1:{route_item.port}"}],
                }
            ]
        }
    location = route_item.path if route_item.path.endswith("/") else f"{route_item.path}/"
    return {
        "handle": [
            {
                "handler": "static_response",
                "status_code": 302,
                "headers": {"Location": [location]},
            }
        ]
    }


def _write_temp_config(config_payload: dict[str, Any]) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
    ) as handle:
        json.dump(config_payload, handle, indent=2)
        handle.write("\n")
        return Path(handle.name)


def _caddy_binary() -> str:
    return os.environ.get("CADDY_BIN", "caddy")
