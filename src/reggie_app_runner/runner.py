import logging
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

import psutil

from reggie_app_runner import caddy, commands, git, tunnel
from reggie_app_runner.config import (
    AppConfig,
    RunnerConfig,
    build_process_env,
    caddy_listen_port,
    read_runner_config,
)


"""Runtime orchestration for multi-app Databricks process hosting."""


LOG = logging.getLogger(__name__)
_APP_READY_TIMEOUT_SECONDS = 60.0
_MONITOR_SLEEP_SECONDS = 1.0


@dataclass
class RunningApp:
    config: AppConfig
    source_dir: Path
    commit_hash: str
    process: subprocess.Popen
    last_update_check_at: float
    next_restart_at: float


@dataclass
class SummaryRuntime:
    process: subprocess.Popen
    port: int
    route_path: str
    state_file: Path


@dataclass(frozen=True)
class RouteBinding:
    name: str
    path: str
    port: int
    strip_path_prefix: bool


def run() -> None:
    runner_config = read_runner_config()
    app_configs = runner_config.apps
    if not app_configs:
        raise RuntimeError("No app configs found under DATABRICKS_APP_RUNNER_<index>_*")

    runtime_root = _runtime_root()
    listen_port = caddy_listen_port()
    shutdown_event = Event()
    running_apps: dict[int, RunningApp] = {}
    caddy_process: subprocess.Popen | None = None
    caddy_config_file: Path | None = None
    tunnel_manager = tunnel.TunnelManager()
    summary_runtime = _start_summary_runtime(runtime_root, app_configs)

    previous_handlers = _install_signal_handlers(shutdown_event)
    try:
        for app_config in app_configs:
            running_app = _start_app_from_remote(
                app_config=app_config,
                runtime_root=runtime_root,
                commit_hash=None,
            )
            running_apps[app_config.index] = running_app

        caddy_process, caddy_config_file = _refresh_routing(
            running_apps=running_apps,
            caddy_process=caddy_process,
            caddy_config_file=caddy_config_file,
            tunnel_manager=tunnel_manager,
            runner_config=runner_config,
            listen_port=listen_port,
            summary_runtime=summary_runtime,
        )

        while not shutdown_event.is_set():
            for app_index, running_app in list(running_apps.items()):
                latest_app = running_apps[app_index]

                if latest_app.process.poll() is not None:
                    now = time.monotonic()
                    if now < latest_app.next_restart_at:
                        continue
                    restart_retry = latest_app.config.redeploy_retry_seconds
                    LOG.warning(
                        "App index=%s died (code=%s). Restarting after %.2fs",
                        app_index,
                        latest_app.process.returncode,
                        restart_retry,
                    )
                    replacement = _attempt_restart_dead_app(
                        running_app=latest_app,
                        runtime_root=runtime_root,
                    )
                    if replacement is None:
                        latest_app.next_restart_at = time.monotonic() + restart_retry
                        running_apps[app_index] = latest_app
                        continue
                    running_apps[app_index] = replacement
                    caddy_process, caddy_config_file = _refresh_routing(
                        running_apps=running_apps,
                        caddy_process=caddy_process,
                        caddy_config_file=caddy_config_file,
                        tunnel_manager=tunnel_manager,
                        runner_config=runner_config,
                        listen_port=listen_port,
                        summary_runtime=summary_runtime,
                    )
                    continue

                if not _should_check_update(latest_app):
                    continue

                latest_app.last_update_check_at = time.monotonic()
                running_apps[app_index] = latest_app
                next_commit_hash = _read_latest_commit_hash(latest_app)
                if not next_commit_hash or next_commit_hash == latest_app.commit_hash:
                    continue

                LOG.info(
                    "App index=%s update detected old=%s new=%s",
                    app_index,
                    latest_app.commit_hash,
                    next_commit_hash,
                )
                replacement = _attempt_cutover_update(
                    running_app=latest_app,
                    runtime_root=runtime_root,
                    target_commit_hash=next_commit_hash,
                )
                if replacement is None:
                    continue
                caddy_process, caddy_config_file = _refresh_routing(
                    running_apps={**running_apps, app_index: replacement},
                    caddy_process=caddy_process,
                    caddy_config_file=caddy_config_file,
                    tunnel_manager=tunnel_manager,
                    runner_config=runner_config,
                    listen_port=listen_port,
                    summary_runtime=summary_runtime,
                )
                _terminate_process_tree(latest_app.process.pid)
                _delete_source_dir(latest_app.source_dir, replacement.source_dir)
                running_apps[app_index] = replacement

            if caddy_process is not None:
                caddy_exit = caddy_process.poll()
                if caddy_exit is not None:
                    raise RuntimeError(f"Caddy exited with code {caddy_exit}")
            for key, exit_code in tunnel_manager.failed_processes():
                raise RuntimeError(f"Tunnel {key} exited with code {exit_code}")
            if summary_runtime.process.poll() is not None:
                raise RuntimeError(
                    f"Summary app exited with code {summary_runtime.process.returncode}"
                )
            time.sleep(_MONITOR_SLEEP_SECONDS)
    finally:
        tunnel_manager.stop_all()
        _stop_process(summary_runtime.process)
        caddy.stop_caddy(caddy_process, caddy_config_file)
        for running_app in reversed(list(running_apps.values())):
            _terminate_process_tree(running_app.process.pid)
        _restore_signal_handlers(previous_handlers)


def _start_app_from_remote(
    app_config: AppConfig,
    runtime_root: Path,
    commit_hash: str | None,
) -> RunningApp:
    source_dir, resolved_commit_hash = git.stage_source(
        source=app_config.source,
        root_dir=runtime_root,
        token=app_config.git_token,
    )
    if commit_hash and resolved_commit_hash != commit_hash:
        LOG.warning(
            "Requested commit %s but staged %s for app index=%s",
            commit_hash,
            resolved_commit_hash,
            app_config.index,
        )
    runtime_app = _start_app_process(app_config, source_dir, resolved_commit_hash)
    _wait_for_port(
        host="127.0.0.1",
        port=runtime_app.config.port,
        owner_process=runtime_app.process,
        timeout_seconds=_APP_READY_TIMEOUT_SECONDS,
    )
    return runtime_app


def _start_app_process(app_config: AppConfig, source_dir: Path, commit_hash: str) -> RunningApp:
    command, run_dir = commands.resolve_entrypoint(source_dir, app_config.command)
    process_env = build_process_env(app_config)
    LOG.info(
        "Starting app index=%s commit=%s path=%s port=%s run_dir=%s command=%s",
        app_config.index,
        commit_hash,
        app_config.route_path,
        app_config.port,
        run_dir,
        command,
    )
    process = subprocess.Popen(
        command,
        cwd=run_dir,
        env=process_env,
        start_new_session=True,
    )
    return RunningApp(
        config=app_config,
        source_dir=source_dir,
        commit_hash=commit_hash,
        process=process,
        last_update_check_at=time.monotonic(),
        next_restart_at=0.0,
    )


def _refresh_caddy(
    caddy_process: subprocess.Popen | None,
    caddy_config_file: Path | None,
    listen_port: int,
    ready_apps: list[AppConfig],
    extra_routes: list[caddy.ProxyRoute] | None = None,
    fallback_route: caddy.ProxyRoute | None = None,
) -> tuple[subprocess.Popen, Path]:
    caddy.stop_caddy(caddy_process, caddy_config_file)
    caddy_payload = caddy.build_config(
        listen_port=listen_port,
        apps=ready_apps,
        extra_routes=extra_routes,
        fallback_route=fallback_route,
    )
    next_process, next_config_file = caddy.start_caddy(caddy_payload)
    LOG.info("Caddy routes refreshed with %s app(s)", len(ready_apps))
    return next_process, next_config_file


def _refresh_tunnels(
    tunnel_manager: tunnel.TunnelManager,
    ready_apps: list[AppConfig],
    runner_config: RunnerConfig,
    listen_port: int,
) -> list[tunnel.TunnelSpec]:
    tunnel_specs = _build_tunnel_specs(ready_apps, runner_config, listen_port)
    tunnel_manager.reconcile(tunnel_specs)
    return tunnel_specs


def _refresh_routing(
    running_apps: dict[int, RunningApp],
    caddy_process: subprocess.Popen | None,
    caddy_config_file: Path | None,
    tunnel_manager: tunnel.TunnelManager,
    runner_config: RunnerConfig,
    listen_port: int,
    summary_runtime: SummaryRuntime,
) -> tuple[subprocess.Popen | None, Path | None]:
    app_configs = [running_app.config for _, running_app in sorted(running_apps.items())]
    route_bindings = _build_route_bindings(running_apps, summary_runtime)
    extra_routes = [
        caddy.ProxyRoute(
            path=binding.path,
            port=binding.port,
            strip_path_prefix=binding.strip_path_prefix,
        )
        for binding in route_bindings
        if binding.name == "app_runner_summary"
    ]
    summary_fallback_route = extra_routes[0] if extra_routes else None
    next_caddy_process, next_caddy_config_file = _refresh_caddy(
        caddy_process=caddy_process,
        caddy_config_file=caddy_config_file,
        listen_port=listen_port,
        ready_apps=app_configs,
        extra_routes=extra_routes,
        fallback_route=summary_fallback_route,
    )
    tunnel_specs = _refresh_tunnels(
        tunnel_manager=tunnel_manager,
        ready_apps=app_configs,
        runner_config=runner_config,
        listen_port=listen_port,
    )
    _write_summary_state(
        summary_runtime=summary_runtime,
        route_bindings=route_bindings,
        tunnel_specs=tunnel_specs,
        listen_port=listen_port,
    )
    return next_caddy_process, next_caddy_config_file


def _build_tunnel_specs(
    ready_apps: list[AppConfig],
    runner_config: RunnerConfig,
    listen_port: int,
) -> list[tunnel.TunnelSpec]:
    specs: list[tunnel.TunnelSpec] = []

    if runner_config.root_public_subdomain:
        if not runner_config.root_tunnel_token:
            raise RuntimeError("Missing tunnel token for root PUBLIC_SUBDOMAIN")
        specs.append(
            tunnel.TunnelSpec(
                key="root",
                port=listen_port,
                subdomain=runner_config.root_public_subdomain,
                token=runner_config.root_tunnel_token,
                remote=runner_config.root_tunnel_remote,
            )
        )

    for app in ready_apps:
        if not app.public_subdomain:
            continue
        app_tunnel_token = app.tunnel_token or runner_config.root_tunnel_token
        app_tunnel_remote = app.tunnel_remote or runner_config.root_tunnel_remote
        if not app_tunnel_token:
            raise RuntimeError(f"Missing tunnel token for app index {app.index}")
        specs.append(
            tunnel.TunnelSpec(
                key=f"app-{app.index}",
                port=app.port,
                subdomain=app.public_subdomain,
                token=app_tunnel_token,
                remote=app_tunnel_remote,
            )
        )
    return specs


def _attempt_cutover_update(
    running_app: RunningApp,
    runtime_root: Path,
    target_commit_hash: str,
) -> RunningApp | None:
    retry_seconds = running_app.config.redeploy_retry_seconds
    while True:
        next_port = _allocate_port()
        next_config = replace(running_app.config, port=next_port)
        try:
            replacement = _start_app_from_remote(
                app_config=next_config,
                runtime_root=runtime_root,
                commit_hash=target_commit_hash,
            )
            if replacement.commit_hash == running_app.commit_hash:
                _terminate_process_tree(replacement.process.pid)
                return None
            return replacement
        except Exception as exc:
            LOG.warning(
                "Cutover retry app index=%s in %.2fs due to: %s",
                running_app.config.index,
                retry_seconds,
                exc,
            )
            time.sleep(retry_seconds)


def _attempt_restart_dead_app(
    running_app: RunningApp,
    runtime_root: Path,
) -> RunningApp | None:
    retry_seconds = running_app.config.redeploy_retry_seconds
    next_port = _allocate_port()
    next_config = replace(running_app.config, port=next_port)
    try:
        replacement = _start_app_from_remote(
            app_config=next_config,
            runtime_root=runtime_root,
            commit_hash=running_app.commit_hash,
        )
        return replacement
    except Exception as exc:
        LOG.warning(
            "Restart failed for app index=%s, retry in %.2fs: %s",
            running_app.config.index,
            retry_seconds,
            exc,
        )
        time.sleep(retry_seconds)
        return None


def _read_latest_commit_hash(running_app: RunningApp) -> str | None:
    try:
        return git.remote_commit_hash(
            running_app.config.source,
            token=running_app.config.git_token,
        )
    except Exception as exc:
        LOG.warning("Failed update check for app index=%s: %s", running_app.config.index, exc)
        return None


def _should_check_update(running_app: RunningApp) -> bool:
    update_interval = running_app.config.update_interval_seconds
    if update_interval is None:
        return False
    return (time.monotonic() - running_app.last_update_check_at) >= update_interval


def _wait_for_port(
    host: str,
    port: int,
    owner_process: subprocess.Popen,
    timeout_seconds: float,
) -> None:
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_seconds:
        if owner_process.poll() is not None:
            raise RuntimeError(
                f"App process exited before opening port {port} with code {owner_process.returncode}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for app to open port {port}")


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def _start_summary_runtime(runtime_root: Path, app_configs: list[AppConfig]) -> SummaryRuntime:
    summary_route_path = _summary_route_path(app_configs)
    summary_port = _allocate_port()
    state_file = runtime_root / "summary-state.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "reggie_app_runner.summary_app",
            "--port",
            str(summary_port),
            "--state-file",
            str(state_file),
            "--base-path",
            summary_route_path,
        ],
        start_new_session=True,
    )
    _wait_for_port(
        host="127.0.0.1",
        port=summary_port,
        owner_process=process,
        timeout_seconds=_APP_READY_TIMEOUT_SECONDS,
    )
    return SummaryRuntime(
        process=process,
        port=summary_port,
        route_path=summary_route_path,
        state_file=state_file,
    )


def _summary_route_path(app_configs: list[AppConfig]) -> str:
    app_paths = {app.route_path for app in app_configs}
    if "/" in app_paths:
        return "/_app_runner"
    return "/"


def _write_summary_state(
    summary_runtime: SummaryRuntime,
    route_bindings: list[RouteBinding],
    tunnel_specs: list[tunnel.TunnelSpec],
    listen_port: int,
) -> None:
    protected_urls: list[dict[str, str]] = []
    public_urls: list[dict[str, str]] = []
    for route_binding in route_bindings:
        protected_urls.append(
            {
                "name": route_binding.name,
                "route": route_binding.path,
                "url": _protected_url(listen_port, route_binding.path),
            }
        )
    for tunnel_spec in tunnel_specs:
        route = "/" if tunnel_spec.key == "root" else _route_for_tunnel_key(
            tunnel_spec.key,
            route_bindings,
        )
        public_urls.append(
            {
                "name": tunnel_spec.key,
                "route": route,
                "url": f"https://{tunnel_spec.subdomain}.{tunnel_spec.remote}",
            }
        )

    payload = {
        "protected_urls": protected_urls,
        "public_urls": public_urls,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    summary_runtime.state_file.write_text(json.dumps(payload, indent=2))


def _route_for_tunnel_key(key: str, route_bindings: list[RouteBinding]) -> str:
    if not key.startswith("app-"):
        return "/"
    try:
        app_index = int(key.split("-", 1)[1])
    except Exception:
        return "/"
    app_name = f"app_{app_index}"
    for route_binding in route_bindings:
        if route_binding.name == app_name:
            return route_binding.path
    return "/"


def _protected_url(listen_port: int, route_path: str) -> str:
    base_url = _protected_base_url(listen_port)
    if route_path == "/":
        return f"{base_url}/"
    return f"{base_url}{route_path}"


def _protected_base_url(listen_port: int) -> str:
    databricks_host = os.environ.get("DATABRICKS_APP_HOST", "").strip()
    if databricks_host:
        host = databricks_host.replace("https://", "").replace("http://", "").rstrip("/")
        return f"https://{host}"
    return f"http://localhost:{listen_port}"


def _build_route_bindings(
    running_apps: dict[int, RunningApp],
    summary_runtime: SummaryRuntime,
) -> list[RouteBinding]:
    bindings: list[RouteBinding] = [
        RouteBinding(
            name="app_runner_summary",
            path=summary_runtime.route_path,
            port=summary_runtime.port,
            strip_path_prefix=summary_runtime.route_path != "/",
        )
    ]
    for _, running_app in sorted(running_apps.items()):
        bindings.append(
            RouteBinding(
                name=f"app_{running_app.config.index}",
                path=running_app.config.route_path,
                port=running_app.config.port,
                strip_path_prefix=running_app.config.strip_path_prefix,
            )
        )
    return bindings


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _install_signal_handlers(shutdown_event: Event) -> dict[int, signal.Handlers]:
    previous_handlers: dict[int, signal.Handlers] = {}

    def _handler(signum: int, _frame: object) -> None:
        LOG.info("Received signal %s, shutting down", signum)
        shutdown_event.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, _handler)
    return previous_handlers


def _restore_signal_handlers(previous_handlers: dict[int, signal.Handlers]) -> None:
    for signum, handler in previous_handlers.items():
        signal.signal(signum, handler)


def _terminate_process_tree(pid: int) -> None:
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    children = process.children(recursive=True)
    targets = children + [process]
    for target in targets:
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            continue
    gone, alive = psutil.wait_procs(targets, timeout=10)
    for target in alive:
        try:
            target.kill()
        except psutil.NoSuchProcess:
            continue
    if alive:
        psutil.wait_procs(alive, timeout=10)
    if gone:
        LOG.info("Stopped %s process(es) for pid %s", len(gone), pid)


def _runtime_root() -> Path:
    configured_root = os.environ.get("DATABRICKS_APP_RUNNER_WORK_DIR")
    if configured_root:
        runtime_root = Path(configured_root)
    else:
        runtime_root = Path("/tmp/reggie-app-runner")
    runtime_root.mkdir(parents=True, exist_ok=True)
    return runtime_root


def _delete_source_dir(old_source_dir: Path, new_source_dir: Path) -> None:
    if old_source_dir == new_source_dir:
        return
    if not old_source_dir.exists():
        return
    if old_source_dir.name.endswith(".tmp"):
        return
    try:
        for child in old_source_dir.iterdir():
            if child.is_dir():
                _delete_tree(child)
            else:
                child.unlink(missing_ok=True)
        old_source_dir.rmdir()
        LOG.info("Deleted old source checkout %s", old_source_dir)
    except Exception as exc:
        LOG.warning("Unable to delete old source dir %s: %s", old_source_dir, exc)


def _delete_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        for child in path.iterdir():
            _delete_tree(child)
        path.rmdir()
        return
    path.unlink(missing_ok=True)
