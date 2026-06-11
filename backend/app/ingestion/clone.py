from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from git import Repo
from git.exc import GitCommandError

MAX_FILE_COUNT = 50_000


def clone_repo_to_temp(url: str, dest: str) -> dict[str, Any]:
    """
    Clone a repository to a temporary folder and return the path for downstream ingestion.

    Caller is responsible for cleanup via `cleanup_clone_path`.
    """
    if not _is_valid_repo_url(url):
        return {
            "ok": False,
            "error": {
                "code": "invalid_url",
                "detail": "Invalid repository URL. Use a valid GitHub HTTP(S) or SSH URL.",
            },
        }

    clone_parent = Path(dest).expanduser().resolve()
    clone_parent.mkdir(parents=True, exist_ok=True)
    clone_target = clone_parent / f"repo_clone_{uuid4().hex}"

    try:
        repo = Repo.clone_from(url, clone_target, depth=1, single_branch=True)
        default_branch = _get_default_branch_name(repo)
        tracked_files = [path for path in repo.git.ls_files().splitlines() if path.strip()]
        if len(tracked_files) > MAX_FILE_COUNT:
            cleanup_clone_path(clone_target)
            return {
                "ok": False,
                "error": {
                    "code": "repo_too_large",
                    "detail": (
                        f"Repository is too large to index ({len(tracked_files)} files). "
                        f"Limit is {MAX_FILE_COUNT} files."
                    ),
                },
            }

        return {
            "ok": True,
            "clone_path": str(clone_target),
            "repo": {
                "name": (
                    _repo_name_from_url(repo.remotes.origin.url)
                    if repo.remotes
                    else clone_target.name
                ),
                "default_branch": default_branch,
                "total_commits": _count_commits(repo, default_branch),
                "tracked_files": tracked_files,
            },
        }
    except GitCommandError as exc:
        cleanup_clone_path(clone_target)
        return _clone_error_response(exc)
    except Exception as exc:  # noqa: BLE001
        cleanup_clone_path(clone_target)
        return {
            "ok": False,
            "error": {
                "code": "clone_failed",
                "detail": f"Failed to clone repository: {exc}",
            },
        }


def cleanup_clone_path(path: str | Path) -> None:
    shutil.rmtree(Path(path), ignore_errors=True)


def clone_repo(url: str, dest: str) -> dict[str, Any]:
    """
    Clone a repository, extract metadata, then cleanup the cloned directory.

    Parameters:
        url: GitHub repository URL.
        dest: Parent directory where a temporary clone directory is created.
    """
    if not _is_valid_repo_url(url):
        return {
            "ok": False,
            "error": {
                "code": "invalid_url",
                "detail": "Invalid repository URL. Use a valid GitHub HTTP(S) or SSH URL.",
            },
        }

    clone_parent = Path(dest).expanduser().resolve()
    clone_parent.mkdir(parents=True, exist_ok=True)
    clone_target = clone_parent / f"repo_clone_{uuid4().hex}"

    repo: Repo | None = None
    try:
        try:
            repo = Repo.clone_from(url, clone_target, depth=1, single_branch=True)
        except GitCommandError as exc:
            return _clone_error_response(exc)

        default_branch = _get_default_branch_name(repo)
        total_commits = _count_commits(repo, default_branch)
        tracked_files = [path for path in repo.git.ls_files().splitlines() if path.strip()]

        if len(tracked_files) > MAX_FILE_COUNT:
            return {
                "ok": False,
                "error": {
                    "code": "repo_too_large",
                    "detail": (
                        f"Repository is too large to index ({len(tracked_files)} files). "
                        f"Limit is {MAX_FILE_COUNT} files."
                    ),
                },
            }

        file_summaries: list[dict[str, Any]] = []
        for rel_path in tracked_files:
            absolute_path = clone_target / rel_path
            if not absolute_path.is_file() or _is_binary_file(absolute_path) or _is_useless_file(rel_path):
                continue

            file_summaries.append(
                {
                    "path": rel_path,
                    "size_bytes": absolute_path.stat().st_size,
                    "owner": _most_frequent_committer_email(repo, rel_path),
                }
            )

        repo_name = clone_target.name
        if repo.remotes:
            repo_name = _repo_name_from_url(repo.remotes.origin.url)

        return {
            "ok": True,
            "repo": {
                "url": url,
                "name": repo_name,
                "default_branch": default_branch,
                "total_commits": total_commits,
                "total_files": len(file_summaries),
            },
            "files": file_summaries,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": {
                "code": "clone_failed",
                "detail": f"Failed to clone and inspect repository: {exc}",
            },
        }
    finally:
        shutil.rmtree(clone_target, ignore_errors=True)


def _is_valid_repo_url(url: str) -> bool:
    if url.startswith("git@"):
        return ":" in url and url.endswith(".git")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ssh"}:
        return False
    if not parsed.netloc or not parsed.path:
        return False
    return True


def _repo_name_from_url(url: str) -> str:
    cleaned = url.rstrip("/")
    name = cleaned.split("/")[-1]
    if name.endswith(".git"):
        return name[:-4]
    return name


def _clone_error_response(exc: GitCommandError) -> dict[str, Any]:
    error_text = str(exc).lower()

    if "authentication failed" in error_text or "could not read username" in error_text:
        return {
            "ok": False,
            "error": {
                "code": "private_repo",
                "detail": (
                    "Repository access failed. If this is a private repository, configure "
                    "credentials (e.g., GITHUB_TOKEN) and retry."
                ),
            },
        }

    if "repository not found" in error_text or "permission denied" in error_text:
        return {
            "ok": False,
            "error": {
                "code": "private_repo",
                "detail": (
                    "Repository not found or access denied. The repository may be private "
                    "or the URL may be incorrect."
                ),
            },
        }

    if "could not resolve host" in error_text or "unable to access" in error_text:
        return {
            "ok": False,
            "error": {
                "code": "invalid_url",
                "detail": "Unable to access repository URL. Verify the URL and network access.",
            },
        }

    return {
        "ok": False,
        "error": {
            "code": "clone_failed",
            "detail": f"Clone failed: {exc}",
        },
    }


def _get_default_branch_name(repo: Repo) -> str:
    try:
        symbolic_ref = repo.git.symbolic_ref("refs/remotes/origin/HEAD")
        # Example: refs/remotes/origin/main
        return symbolic_ref.rsplit("/", maxsplit=1)[-1]
    except GitCommandError:
        pass

    try:
        return repo.active_branch.name
    except TypeError:
        pass
    except Exception:  # noqa: BLE001
        pass

    if repo.heads:
        return repo.heads[0].name
    return "unknown"


def _count_commits(repo: Repo, default_branch: str) -> int:
    try:
        if default_branch and default_branch != "unknown":
            return int(repo.git.rev_list("--count", default_branch))
    except Exception:  # noqa: BLE001
        pass

    try:
        return int(repo.git.rev_list("--count", "--all"))
    except Exception:  # noqa: BLE001
        return 0


def _most_frequent_committer_email(repo: Repo, rel_path: str) -> str:
    try:
        log_output = repo.git.log("--follow", "--format=%ae", "--", rel_path)
    except GitCommandError:
        return ""

    emails = [line.strip().lower() for line in log_output.splitlines() if line.strip()]
    if not emails:
        return ""

    counts = Counter(emails)
    max_count = max(counts.values())
    top_committers = sorted(email for email, count in counts.items() if count == max_count)
    return top_committers[0]


def _is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as file_obj:
            sample = file_obj.read(8192)
        return b"\x00" in sample
    except OSError:
        return True


def _is_useless_file(rel_path: str) -> bool:
    """Filter out lock files, cache, and hidden files that clutter the graph."""
    path = Path(rel_path)
    name = path.name.lower()
    
    # Common lockfiles
    if name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "gemfile.lock"}:
        return True
        
    # Hidden files/folders except github workflows/configs if they were useful, but mostly we ignore
    if name.startswith(".") and name not in {".env.example", ".github"}:
        return True
        
    # Cache and build outputs
    if "__pycache__" in rel_path or name.endswith(".pyc"):
        return True
        
    # Standard ignore
    if name in {"ds_store", "thumbs.db"}:
        return True
        
    return False
