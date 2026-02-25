import pytest

from reggie_app_runner import commands


def test_resolve_command_uses_explicit_command(tmp_path):
    source_dir = tmp_path
    explicit_command = ["./run.sh"]
    assert commands.resolve_command(source_dir, explicit_command) == explicit_command


def test_read_command_from_app_yaml_string(tmp_path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("command: pixi run dev\n")

    assert commands.read_command_from_app_yaml(tmp_path) == ["pixi", "run", "dev"]


def test_read_command_from_app_yaml_list(tmp_path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("command:\n  - ./run.sh\n  - --debug\n")

    assert commands.read_command_from_app_yaml(tmp_path) == ["./run.sh", "--debug"]


def test_read_command_from_app_yaml_requires_file(tmp_path):
    with pytest.raises(ValueError):
        commands.read_command_from_app_yaml(tmp_path)


def test_read_command_from_nested_single_app_yaml(tmp_path):
    nested = tmp_path / "service"
    nested.mkdir(parents=True, exist_ok=True)
    app_yaml = nested / "app.yaml"
    app_yaml.write_text("command: pixi run dev\n")

    command = commands.read_command_from_app_yaml(tmp_path)
    resolved_command, resolved_dir = commands.resolve_entrypoint(tmp_path, [])

    assert command == ["pixi", "run", "dev"]
    assert resolved_command == ["pixi", "run", "dev"]
    assert resolved_dir == nested


def test_read_command_from_app_yaml_rejects_multiple_nested_files(tmp_path):
    nested_a = tmp_path / "a"
    nested_b = tmp_path / "b"
    nested_a.mkdir(parents=True, exist_ok=True)
    nested_b.mkdir(parents=True, exist_ok=True)
    (nested_a / "app.yaml").write_text("command: ./run-a.sh\n")
    (nested_b / "app.yaml").write_text("command: ./run-b.sh\n")

    with pytest.raises(ValueError, match="Multiple app.yaml files found"):
        commands.read_command_from_app_yaml(tmp_path)
