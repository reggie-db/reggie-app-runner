import logging
import os
import subprocess
from dataclasses import dataclass


"""Process manager for portrc HTTP tunnels."""


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TunnelSpec:
    key: str
    port: int
    subdomain: str
    token: str
    remote: str


@dataclass
class TunnelProcess:
    spec: TunnelSpec
    process: subprocess.Popen


class TunnelManager:
    def __init__(self) -> None:
        self._processes: dict[str, TunnelProcess] = {}

    def reconcile(self, desired_specs: list[TunnelSpec]) -> None:
        desired_map = {spec.key: spec for spec in desired_specs}

        for key in list(self._processes):
            if key not in desired_map:
                self._stop_key(key)

        for spec in desired_specs:
            current = self._processes.get(spec.key)
            if current and current.process.poll() is None and current.spec == spec:
                continue
            if current:
                self._stop_key(spec.key)
            self._processes[spec.key] = TunnelProcess(spec=spec, process=_start_process(spec))

    def stop_all(self) -> None:
        for key in list(self._processes):
            self._stop_key(key)

    def failed_processes(self) -> list[tuple[str, int]]:
        failures: list[tuple[str, int]] = []
        for key, current in self._processes.items():
            exit_code = current.process.poll()
            if exit_code is not None:
                failures.append((key, exit_code))
        return failures

    def _stop_key(self, key: str) -> None:
        current = self._processes.pop(key, None)
        if not current:
            return
        process = current.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _start_process(spec: TunnelSpec) -> subprocess.Popen:
    LOG.info(
        "Starting tunnel key=%s subdomain=%s remote=%s port=%s",
        spec.key,
        spec.subdomain,
        spec.remote,
        spec.port,
    )
    return subprocess.Popen(
        [
            "portrc",
            "--remote",
            spec.remote,
            "--token",
            spec.token,
            "http",
            str(spec.port),
            "--subdomain",
            spec.subdomain,
        ],
        env={
            **os.environ,
            "PORTR_DISABLE_CONFIG": "true",
            "PORTR_DISABLE_TUI": "true",
        },
        start_new_session=True,
    )
