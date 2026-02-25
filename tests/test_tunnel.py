from reggie_app_runner import tunnel


class _FakeProcess:
    def __init__(self):
        self._poll = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0

    def wait(self, timeout: int):
        return 0

    def kill(self):
        self.killed = True
        self._poll = 0


def test_tunnel_manager_restarts_when_port_changes(monkeypatch):
    created: list[_FakeProcess] = []

    def _fake_start(spec):
        process = _FakeProcess()
        created.append(process)
        return process

    monkeypatch.setattr(tunnel, "_start_process", _fake_start)
    manager = tunnel.TunnelManager()

    first = tunnel.TunnelSpec(
        key="app-1",
        port=4100,
        subdomain="abc",
        token="token",
        remote="nectus.io",
    )
    second = tunnel.TunnelSpec(
        key="app-1",
        port=4200,
        subdomain="abc",
        token="token",
        remote="nectus.io",
    )

    manager.reconcile([first])
    manager.reconcile([second])

    assert len(created) == 2
    assert created[0].terminated is True
    assert created[1].terminated is False


def test_tunnel_manager_stops_removed_keys(monkeypatch):
    created: list[_FakeProcess] = []

    def _fake_start(spec):
        process = _FakeProcess()
        created.append(process)
        return process

    monkeypatch.setattr(tunnel, "_start_process", _fake_start)
    manager = tunnel.TunnelManager()
    spec = tunnel.TunnelSpec(
        key="root",
        port=8000,
        subdomain="public",
        token="token",
        remote="nectus.io",
    )

    manager.reconcile([spec])
    manager.reconcile([])

    assert created[0].terminated is True
