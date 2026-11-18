from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pygit2
import pytest

from rootsh.hoststate import expand_glob, probe
from rootsh.hoststate.fs import walk
from rootsh.hoststate.git import from_flags
from rootsh.hoststate.sensitivity import CLASSES, classify, contained_prefix_classes

HOME = "/home/u"
SIGNATURE = pygit2.Signature("rootsh-test", "test@example.invalid")


def commit(repo: pygit2.Repository, *paths: str) -> None:
    for path in paths:
        repo.index.add(path)
    repo.index.write()
    tree = repo.index.write_tree()
    parents = [] if repo.head_is_unborn else [repo.head.target]
    repo.create_commit("HEAD", SIGNATURE, SIGNATURE, "test", tree, parents)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git = pygit2.init_repository(str(root))
    (root / ".gitignore").write_text("build/\n*.log\n")
    (root / "clean.txt").write_text("committed\n")
    (root / "edited.txt").write_text("v1\n")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    commit(git, ".gitignore", "clean.txt", "edited.txt", "src/a.py")
    (root / "edited.txt").write_text("v2\n")
    (root / "new.txt").write_text("untracked\n")
    (root / "app.log").write_text("ignored\n")
    (root / "build").mkdir()
    (root / "build" / "out.bin").write_text("ignored dir\n")
    return root


def state(path: Path, project: Path | None = None) -> str:
    project_root = str(project) if project else None
    return probe([str(path)], project_root=project_root, home=HOME)[str(path)].git


# --- git ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("clean.txt", "clean"),
        ("edited.txt", "modified"),
        ("new.txt", "untracked"),
        ("app.log", "ignored"),
        ("build", "ignored"),
        ("build/out.bin", "ignored"),
        ("src", "clean"),
        (".", "modified"),  # the worst thing inside the work tree
    ],
)
def test_git_states(repo: Path, relative: str, expected: str) -> None:
    assert state(repo / relative, repo) == expected


def test_staged_only_change_is_recoverable(repo: Path) -> None:
    (repo / "clean.txt").write_text("staged\n")
    git = pygit2.Repository(str(repo))
    git.index.add("clean.txt")
    git.index.write()
    assert state(repo / "clean.txt") == "clean"


def test_directory_with_an_untracked_file_is_untracked(repo: Path) -> None:
    (repo / "src" / "scratch.py").write_text("tmp\n")
    assert state(repo / "src") == "untracked"


def test_outside_a_repository(tmp_path: Path) -> None:
    loose = tmp_path / "loose.txt"
    loose.write_text("x")
    assert state(loose) == "not_in_repo"


def test_git_internals_and_missing_paths_are_unknown(repo: Path) -> None:
    assert state(repo / ".git") == "unknown"
    missing = probe([str(repo / "nope")], project_root=str(repo), home=HOME)[str(repo / "nope")]
    assert (missing.exists, missing.kind, missing.git) == (False, "missing", "unknown")


def test_flag_mapping() -> None:
    flags = pygit2.enums.FileStatus
    assert from_flags(0) == "clean"
    assert from_flags(flags.INDEX_NEW) == "clean"
    assert from_flags(flags.INDEX_MODIFIED | flags.WT_MODIFIED) == "modified"
    assert from_flags(flags.WT_DELETED) == "modified"
    assert from_flags(flags.WT_NEW) == "untracked"
    assert from_flags(flags.IGNORED) == "ignored"


# --- hostile repository ------------------------------------------------------------


def _hostile_repo(root: Path, markers: Path) -> None:
    git = pygit2.init_repository(str(root))
    (root / ".gitattributes").write_text("* filter=evil\n")
    (root / "a.txt").write_text("v1\n")
    commit(git, ".gitattributes", "a.txt")
    git.config["core.fsmonitor"] = f"touch {markers / 'fsmonitor-ran'}"
    git.config["filter.evil.clean"] = f"sh -c 'touch {markers / 'filter-ran'}; cat'"
    (root / "a.txt").write_text("v2, a different length\n")  # forces a content check


def test_probing_a_hostile_repository_runs_none_of_its_commands(tmp_path: Path) -> None:
    root, markers = tmp_path / "hostile", tmp_path / "markers"
    root.mkdir()
    markers.mkdir()
    _hostile_repo(root, markers)
    states = probe([str(root), str(root / "a.txt")], project_root=str(root), home=HOME)
    assert states[str(root / "a.txt")].git == "modified"
    assert list(markers.iterdir()) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="control needs the git CLI")
def test_control_the_git_cli_does_run_them(tmp_path: Path) -> None:
    """Proves the hostile-repo test can fail: the git CLI executes repo config."""
    root, markers = tmp_path / "hostile", tmp_path / "markers"
    root.mkdir()
    markers.mkdir()
    _hostile_repo(root, markers)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    # Fixed argv; the point is to show that git runs commands from repo config.
    subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=root,
        env=env,
        capture_output=True,
        check=False,
    )
    assert list(markers.iterdir()) != []


# --- filesystem ------------------------------------------------------------------


def test_symlink_escaping_the_project_is_flagged(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "link").symlink_to(outside)
    result = probe([str(project / "link")], project_root=str(project), home=HOME)
    link = result[str(project / "link")]
    assert link.kind == "symlink"
    assert link.real_path == str(outside)
    assert not link.in_project


def test_directory_coverage_counts_files_and_bytes(tmp_path: Path) -> None:
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "d" / "a").write_bytes(b"12345")
    (tmp_path / "d" / "e" / "b").write_bytes(b"123")
    (tmp_path / "d" / "link").symlink_to("/etc")  # not followed
    result = probe([str(tmp_path / "d")], project_root=str(tmp_path), home=HOME)
    d = result[str(tmp_path / "d")]
    assert (d.kind, d.files, d.bytes, d.capped) == ("dir", 2, 8, False)
    assert "system" not in d.sensitivity  # the /etc symlink was not walked


def test_walk_caps(tmp_path: Path) -> None:
    for i in range(30):
        (tmp_path / f"f{i}").write_text("x")
    assert walk(str(tmp_path), lambda _: None, max_entries=10).capped
    deep = tmp_path / "deep"
    path = deep
    for i in range(5):
        path = path / f"l{i}"
    path.mkdir(parents=True)
    assert walk(str(deep), lambda _: None, max_depth=3).capped
    assert not walk(str(deep), lambda _: None).capped
    assert walk(str(tmp_path), lambda _: None, max_seconds=0.0).capped


def test_glob_expansion(tmp_path: Path) -> None:
    for name in ("a.log", "b.log", ".hidden.log", "c.txt"):
        (tmp_path / name).write_text("x")
    matches, capped = expand_glob(str(tmp_path / "*.log"))
    assert [Path(m).name for m in matches] == ["a.log", "b.log"]
    assert not capped


def test_probe_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="absolute"):
        probe(["relative/path"], project_root=None, home=HOME)


# --- sensitivity -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/u/.ssh/id_rsa", {"secret"}),
        ("/w/.env", {"secret"}),
        ("/w/.env.production", {"secret"}),
        ("/w/certs/server.pem", {"secret"}),
        ("/w/db/migrations/0001.py", {"data_store"}),
        ("/w/app.sqlite3", {"data_store"}),
        ("/w/.github/workflows/ci.yml", {"ci"}),
        ("/w/.git/config", {"vcs_internal"}),
        ("/w/.git/hooks/pre-commit", {"vcs_internal", "persistence"}),
        ("/home/u/.bashrc", {"persistence"}),
        ("/w/CLAUDE.md", {"agent_config"}),
        ("/home/u/.claude/settings.json", {"agent_config"}),
        ("/w/.opencode/plugins/x.ts", {"agent_config"}),
        ("/etc/claude-code/managed-settings.json", {"rootsh", "system"}),
        ("/etc/passwd", {"system"}),
        ("/w/src/app.py", set()),
        ("/w/environment.md", set()),
    ],
)
def test_classify(path: str, expected: set[str]) -> None:
    assert classify(path, HOME) == expected


def test_contained_locations_count_for_a_directory() -> None:
    assert "secret" in contained_prefix_classes("/home/u", HOME)
    assert "persistence" in contained_prefix_classes("/home/u", HOME)
    assert contained_prefix_classes("/w", HOME) == frozenset()


def test_walk_finds_sensitive_files_inside_a_directory(tmp_path: Path) -> None:
    (tmp_path / "app" / "config").mkdir(parents=True)
    (tmp_path / "app" / "config" / ".env").write_text("TOKEN=x")
    result = probe([str(tmp_path / "app")], project_root=str(tmp_path), home=HOME)
    assert "secret" in result[str(tmp_path / "app")].sensitivity


def test_every_class_has_a_test_case() -> None:
    covered = {"secret", "data_store", "ci", "vcs_internal", "persistence", "agent_config"}
    assert covered | {"rootsh", "system"} == CLASSES


def test_a_new_file_inside_a_symlinked_project_is_in_the_project(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    target = str(tmp_path / "link" / "not_created_yet.txt")
    state = probe([target], project_root=str(tmp_path / "link"), home=HOME)[target]
    assert (state.exists, state.in_project) == (False, True)
