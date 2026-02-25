import json

from reggie_app_runner import config


def test_read_app_configs_applies_defaults(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_0_SOURCE", "https://github.com/org/repo")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_0_ENV", json.dumps({"HELLO": "WORLD"}))

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 0)
    assert app_config.index == 0
    assert app_config.source == "https://github.com/org/repo"
    assert app_config.route_path == "/repo"
    assert app_config.strip_path_prefix is True
    assert app_config.env == {"HELLO": "WORLD"}
    assert app_config.command == []
    assert app_config.port > 0
    assert app_config.tunnel_remote == "nectus.io"
    assert app_config.update_interval_seconds == 30.0
    assert app_config.redeploy_retry_seconds == 5.0


def test_read_app_configs_reads_command_and_path(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_2_SOURCE", "https://github.com/org/other")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_2_COMMAND", "pixi run start")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_2_PATH", "my-app")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_2_STRIP_PATH_PREFIX", "false")

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 2)

    assert app_config.command == ["pixi", "run", "start"]
    assert app_config.route_path == "/my-app"
    assert app_config.strip_path_prefix is False


def test_read_app_configs_reads_comma_separated_command(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_3_SOURCE", "https://github.com/org/three")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_3_COMMAND", "pixi, run, xyz")

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 3)

    assert app_config.command == ["pixi", "run", "xyz"]


def test_read_app_configs_defaults_path_from_source_repo_name(monkeypatch):
    monkeypatch.setenv(
        "DATABRICKS_APP_RUNNER_4_SOURCE",
        "git+https://github.com/jjaiwant328/racetrac-store-intelligence.git@reggie-chat",
    )

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 4)

    assert app_config.route_path == "/racetrac-store-intelligence"


def test_root_public_subdomain_applies_to_runner_only(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_PUBLIC_SUBDOMAIN", "root-subdomain")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_0_SOURCE", "https://github.com/org/repo")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_TUNNEL_TOKEN", "runner-token")

    runner_config = config.read_runner_config()
    app_config = next(item for item in runner_config.apps if item.index == 0)

    assert runner_config.root_public_subdomain == "root-subdomain"
    assert app_config.public_subdomain is None
    assert runner_config.root_tunnel_token == "runner-token"


def test_app_public_subdomain_overrides_root_behavior(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_PUBLIC_SUBDOMAIN", "root-subdomain")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_0_SOURCE", "https://github.com/org/repo")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_0_PUBLIC_SUBDOMAIN", "app-subdomain")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_TUNNEL_TOKEN", "runner-token")

    runner_config = config.read_runner_config()
    app_config = next(item for item in runner_config.apps if item.index == 0)

    assert runner_config.root_public_subdomain == "root-subdomain"
    assert app_config.public_subdomain == "app-subdomain"


def test_runner_tunnel_env_fallback_to_legacy_names(monkeypatch):
    monkeypatch.delenv("DATABRICKS_APP_RUNNER_TUNNEL_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_RUNNER_TUNNEL_REMOTE", raising=False)
    monkeypatch.setenv("DATABRICKS_APP_TUNNEL_TOKEN", "legacy-token")
    monkeypatch.setenv("DATABRICKS_APP_TUNNEL_REMOTE", "legacy.example")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_0_SOURCE", "https://github.com/org/repo")

    runner_config = config.read_runner_config()
    app_config = next(item for item in runner_config.apps if item.index == 0)

    assert runner_config.root_tunnel_token == "legacy-token"
    assert runner_config.root_tunnel_remote == "legacy.example"
    assert app_config.tunnel_token == "legacy-token"
    assert app_config.tunnel_remote == "legacy.example"


def test_read_app_configs_env_string_supports_yaml_map(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_5_SOURCE", "https://github.com/org/repo")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_5_ENV", "{EXAMPLE_MODE: local, FLAG: true}")

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 5)

    assert app_config.env == {"EXAMPLE_MODE": "local", "FLAG": "True"}


def test_update_interval_falsey_disables_checks(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_6_SOURCE", "https://github.com/org/repo")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_6_UPDATE_INTERVAL_SECONDS", "0")

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 6)

    assert app_config.update_interval_seconds is None


def test_redeploy_retry_seconds_reads_override(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_7_SOURCE", "https://github.com/org/repo")
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_7_REDEPLOY_RETRY_SECONDS", "9")

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 7)

    assert app_config.redeploy_retry_seconds == 9.0


def test_build_process_env_strips_runner_virtualenv(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_RUNNER_8_SOURCE", "https://github.com/org/repo")
    runner_venv = str((config.Path.cwd() / ".venv" / "bin").resolve())
    monkeypatch.setenv("PATH", f"{runner_venv}:/usr/bin")
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/runner-venv")

    app_configs = config.read_app_configs()
    app_config = next(item for item in app_configs if item.index == 8)
    process_env = config.build_process_env(app_config)

    assert "VIRTUAL_ENV" not in process_env
    assert process_env["PATH"] == "/usr/bin"
