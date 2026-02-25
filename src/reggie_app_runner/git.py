import logging
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import giturlparse
import requirements
from requirements.requirement import Requirement


"""Git source helpers for commit-based source staging."""


LOG = logging.getLogger(__name__)
_GITHUB_TREE_PATH_PATTERN = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/tree/(?P<branch>.+)$"
)


def is_url(source: str) -> bool:
    """Return True when the source is a valid git URL or git requirement string."""
    return _git_requirement(source) or giturlparse.validate(source)


def source_url(source: str) -> str:
    """Return a normalized URL from either raw URL or requirement notation."""
    if requirement := _git_requirement(source):
        return _normalize_source_url(str(requirement.uri))
    return _normalize_source_url(source)


def revision(source: str) -> str:
    """Resolve a preferred revision from a requirement string or URL, default main."""
    if requirement := _git_requirement(source):
        if requirement.revision:
            return requirement.revision
    if pip_style_revision := _pip_style_revision(source):
        return pip_style_revision
    if tree_branch := _github_tree_branch(source):
        return tree_branch
    if git_url := _git_url(source_url(source)):
        if git_url.branch:
            return git_url.branch
    return "main"


def remote_commit_hash(source: str, token: str | None = None) -> str:
    """Fetch the remote commit hash for the given source and optional token."""
    remote_revision = revision(source)
    errors: list[str] = []
    for remote_url in _remote_url_candidates(source, token):
        try:
            output = _git_command("ls-remote", remote_url, remote_revision)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if output.strip():
            return output.strip().split()[0]
    joined_errors = " | ".join(errors) if errors else "no candidate URLs attempted"
    raise RuntimeError(
        f"No remote commit found for {source}@{remote_revision}. Attempts: {joined_errors}"
    )


def clone(source: str, dest: str | Path, token: str | None = None) -> None:
    """Shallow clone source into dest at the resolved revision using optional token."""
    remote_revision = revision(source)
    errors: list[str] = []
    for remote_url in _remote_url_candidates(source, token):
        try:
            _git_command(
                "clone",
                "--branch",
                remote_revision,
                "--single-branch",
                "--depth",
                "1",
                remote_url,
                str(Path(dest).resolve()),
            )
            return
        except RuntimeError as exc:
            errors.append(str(exc))
    joined_errors = " | ".join(errors) if errors else "no candidate URLs attempted"
    raise RuntimeError(f"Unable to clone source {source}. Attempts: {joined_errors}")


def stage_source(source: str, root_dir: Path, token: str | None = None) -> tuple[Path, str]:
    """Clone source into a commit-keyed temp directory and return path and commit."""
    commit_hash = remote_commit_hash(source, token=token)
    checkout_dir = root_dir / "sources" / commit_hash
    if checkout_dir.exists():
        LOG.info("Using existing source checkout at %s", checkout_dir)
        return checkout_dir, commit_hash

    temp_checkout = root_dir / "sources" / f"{commit_hash}.tmp"
    if temp_checkout.exists():
        shutil.rmtree(temp_checkout)
    temp_checkout.mkdir(parents=True, exist_ok=True)
    clone(source, temp_checkout, token=token)

    if checkout_dir.exists():
        shutil.rmtree(checkout_dir)
    temp_checkout.rename(checkout_dir)
    return checkout_dir, commit_hash


def _git_command(*args: str) -> str:
    command = ["git", *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or "no stderr/stdout provided"
        raise RuntimeError(
            f"git command failed: {' '.join(command)} :: {details}"
        ) from exc
    if result.stderr.strip():
        LOG.info(result.stderr.strip())
    return result.stdout


def _git_url(source: str) -> giturlparse.GitUrlParsed:
    """Parse a git URL from a requirement string or raw URL, else return None."""
    if requirement := _git_requirement(source):
        source = requirement.uri
    if giturlparse.validate(source):
        return giturlparse.parse(source)
    return None


def _normalize_source_url(source: str) -> str:
    source = source.strip()
    if source.startswith("git+"):
        source = source[len("git+") :]
        source = _strip_pip_style_revision(source)
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"} and parsed.hostname == "github.com":
        if match := _GITHUB_TREE_PATH_PATTERN.match(parsed.path):
            owner = match.group("owner")
            repo = match.group("repo")
            normalized_path = f"/{owner}/{repo}.git"
            return urlunparse(
                (parsed.scheme, parsed.netloc, normalized_path, "", "", "")
            )
    return source


def _strip_pip_style_revision(source: str) -> str:
    no_fragment = source.split("#", 1)[0]
    if ".git@" in no_fragment:
        prefix, _ = no_fragment.rsplit("@", 1)
        return prefix
    return no_fragment


def _pip_style_revision(source: str) -> str | None:
    parsed_source = source.strip()
    if not parsed_source.startswith("git+"):
        return None
    no_fragment = parsed_source.split("#", 1)[0]
    if ".git@" not in no_fragment:
        return None
    revision_value = no_fragment.rsplit("@", 1)[1].strip()
    return revision_value or None


def _github_tree_branch(source: str) -> str | None:
    parsed_source = source.strip()
    if requirement := _git_requirement(source):
        parsed_source = str(requirement.uri)
    if parsed_source.startswith("git+"):
        parsed_source = parsed_source[len("git+") :]
    parsed = urlparse(parsed_source)
    if parsed.scheme in {"http", "https"} and parsed.hostname == "github.com":
        if match := _GITHUB_TREE_PATH_PATTERN.match(parsed.path):
            return unquote(match.group("branch"))
    return None


def _tokenized_url(raw_url: str, token: str | None) -> str:
    if not token:
        return raw_url
    parsed = urlparse(raw_url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return raw_url
    if parsed.username:
        return raw_url
    netloc = f"{token}@{parsed.netloc}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def _remote_url_candidates(source: str, token: str | None) -> list[str]:
    raw = source_url(source)
    candidates: list[str] = []
    if git_url := _git_url(raw):
        ssh_url = str(git_url.url2ssh) if git_url.url2ssh else None
        if ssh_url:
            candidates.append(ssh_url)
    candidates.append(_tokenized_url(raw, token))

    unique_candidates: list[str] = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def _git_requirement(source: str) -> Requirement:
    """Return a parsed requirements entry when the source is a git VCS spec."""
    try:
        for req in requirements.parse(source):
            if "git" == req.vcs and req.uri:
                return req
    except Exception:
        pass
    return None
