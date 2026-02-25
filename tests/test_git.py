from reggie_app_runner import git


def test_revision_supports_pip_git_format():
    source = "git+https://github.com/jjaiwant328/racetrac-store-intelligence.git@reggie-chat"
    assert git.revision(source) == "reggie-chat"


def test_source_url_normalizes_github_tree_format():
    source = "https://github.com/jjaiwant328/racetrac-store-intelligence/tree/reggie-chat"
    assert (
        git.source_url(source)
        == "https://github.com/jjaiwant328/racetrac-store-intelligence.git"
    )
    assert git.revision(source) == "reggie-chat"


def test_source_url_supports_git_plus_prefix():
    source = "git+https://github.com/reggie-db/store-intelligence.git"
    assert git.source_url(source) == "https://github.com/reggie-db/store-intelligence.git"


def test_source_url_strips_pip_style_branch_suffix():
    source = "git+https://github.com/jjaiwant328/racetrac-store-intelligence.git@reggie-chat"
    assert (
        git.source_url(source)
        == "https://github.com/jjaiwant328/racetrac-store-intelligence.git"
    )
    assert git.revision(source) == "reggie-chat"


def test_is_url_supports_requirement_and_plain_urls():
    assert git.is_url("https://github.com/reggie-db/store-intelligence")
    assert git.is_url("https://github.com/reggie-db/store-intelligence.git")
    assert git.is_url(
        "git+https://github.com/jjaiwant328/racetrac-store-intelligence.git@reggie-chat"
    )


def test_remote_url_candidates_prefer_ssh_for_github():
    candidates = git._remote_url_candidates("https://github.com/reggie-db/store-intelligence.git", None)
    assert candidates[0] == "git@github.com:reggie-db/store-intelligence.git"
    assert candidates[1] == "https://github.com/reggie-db/store-intelligence.git"


def test_remote_commit_hash_falls_back_from_ssh_to_https(monkeypatch):
    calls: list[str] = []

    def _fake_git_command(*args: str) -> str:
        remote = args[1]
        calls.append(remote)
        if remote.startswith("git@github.com:"):
            raise RuntimeError("ssh failed")
        return "abc123\trefs/heads/main\n"

    monkeypatch.setattr(git, "_git_command", _fake_git_command)
    commit_hash = git.remote_commit_hash("https://github.com/reggie-db/store-intelligence.git")

    assert commit_hash == "abc123"
    assert calls[0].startswith("git@github.com:")
    assert calls[1].startswith("https://github.com/")
