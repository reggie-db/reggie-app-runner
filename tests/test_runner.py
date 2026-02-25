from types import SimpleNamespace

from reggie_app_runner import runner
from reggie_app_runner.config import AppConfig, RunnerConfig


class _FakePsutilProcess:
    def __init__(self, pid: int, children: list["_FakePsutilProcess"] | None = None):
        self.pid = pid
        self._children = children or []
        self.terminated = False
        self.killed = False

    def children(self, recursive: bool = True):
        return self._children

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_terminate_process_tree_terminates_and_kills_alive_processes(monkeypatch):
    child_a = _FakePsutilProcess(2)
    child_b = _FakePsutilProcess(3)
    root = _FakePsutilProcess(1, [child_a, child_b])

    def _fake_wait_procs(targets, timeout):
        assert timeout == 10
        return [], list(targets)

    fake_psutil = SimpleNamespace(
        Process=lambda pid: root,
        NoSuchProcess=RuntimeError,
        wait_procs=_fake_wait_procs,
    )
    monkeypatch.setattr(runner, "psutil", fake_psutil)

    runner._terminate_process_tree(1)

    assert root.terminated is True
    assert child_a.terminated is True
    assert child_b.terminated is True
    assert root.killed is True
    assert child_a.killed is True
    assert child_b.killed is True


def test_build_tunnel_specs_uses_root_and_app_scopes():
    app_config = AppConfig(
        index=0,
        source="https://github.com/org/repo",
        route_path="/app_0",
        strip_path_prefix=True,
        env={},
        command=["pixi", "run", "start"],
        port=4100,
        git_token=None,
        public_subdomain="app-sub",
        tunnel_token="app-token",
        tunnel_remote="app.remote",
        update_interval_seconds=30.0,
        redeploy_retry_seconds=5.0,
    )
    runner_config = RunnerConfig(
        apps=[app_config],
        root_public_subdomain="root-sub",
        root_tunnel_token="root-token",
        root_tunnel_remote="root.remote",
    )

    specs = runner._build_tunnel_specs([app_config], runner_config, listen_port=8000)

    assert len(specs) == 2
    assert specs[0].key == "root"
    assert specs[0].port == 8000
    assert specs[1].key == "app-0"
    assert specs[1].port == 4100
    assert specs[1].token == "app-token"


def test_summary_route_path_defaults_to_root():
    app_config = AppConfig(
        index=0,
        source="https://github.com/org/repo",
        route_path="/app_0",
        strip_path_prefix=True,
        env={},
        command=["pixi", "run", "start"],
        port=4100,
        git_token=None,
        public_subdomain=None,
        tunnel_token=None,
        tunnel_remote="nectus.io",
        update_interval_seconds=30.0,
        redeploy_retry_seconds=5.0,
    )
    assert runner._summary_route_path([app_config]) == "/"


def test_summary_route_path_uses_app_runner_prefix_when_root_taken():
    app_config = AppConfig(
        index=0,
        source="https://github.com/org/repo",
        route_path="/",
        strip_path_prefix=True,
        env={},
        command=["pixi", "run", "start"],
        port=4100,
        git_token=None,
        public_subdomain=None,
        tunnel_token=None,
        tunnel_remote="nectus.io",
        update_interval_seconds=30.0,
        redeploy_retry_seconds=5.0,
    )
    assert runner._summary_route_path([app_config]) == "/_app_runner"
