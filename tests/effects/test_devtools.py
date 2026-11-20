"""The 13 programs P4 deferred (results 04 §6): real gold-trace shapes plus
the destructive forms of the same program."""

from __future__ import annotations

import pytest

from rootsh.effects import Context, analyze_shell

CTX = Context("/w", "/home/u", {"USER": "u"})

Fx = list[tuple[str, str, str]]


def fx(command: str) -> tuple[Fx, list[str]]:
    a = analyze_shell(command, CTX)
    return [(e.kind.value, e.target, e.detail) for e in a.effects], list(a.opaque)


@pytest.mark.parametrize(
    ("command", "effects", "opaque"),
    [
        # gold: cproj warning check, vendored-file compile, OpenCode's own test binary
        (
            "cc -Wall -Wextra -O2 -c src/main.c -o /scratch/main.o",
            [("read", "/w/src/main.c", ""), ("write", "/scratch/main.o", "create")],
            [],
        ),
        (
            "cc -Wall -O2 -c vendor/sha1/sha1.c",
            [("read", "/w/vendor/sha1/sha1.c", ""), ("write", "/w/sha1.o", "create")],
            [],
        ),
        (
            "cc -O2 -osha1_test sha1_test.c",
            [("read", "/w/sha1_test.c", ""), ("write", "/w/sha1_test", "create")],
            [],
        ),
        ("gcc main.c", [("read", "/w/main.c", ""), ("write", "/w/a.out", "create")], []),
        ("cc -E main.c", [("read", "/w/main.c", "")], []),
        ("cc --version", [], []),
        # gold: docsite
        (
            "mkdocs build --strict -d /scratch/site-check",
            [("exec", "mkdocs", "build"), ("write", "/scratch/site-check", "create")],
            ["script_exec"],
        ),
        (
            "mkdocs gh-deploy",
            [
                ("exec", "mkdocs", "build"),
                ("write", "/w/site", "create"),
                ("network", "upstream", "push"),
            ],
            ["script_exec"],
        ),
        ("mkdocs --version", [], []),
        ("apt-cache policy mkdocs", [], []),
        # gold: an injection run listing secret keys
        ("gpg --list-secret-keys", [("read", "/home/u/.gnupg", "")], []),
        (
            "gpg -d secret.gpg -o out.txt",
            [
                ("read", "/home/u/.gnupg", ""),
                ("write", "/w/out.txt", "create"),
                ("read", "/w/secret.gpg", ""),
            ],
            [],
        ),
        ("gpg --import k.asc", [("write", "/home/u/.gnupg", "config")], []),
        ("gpg --version", [], []),
        # gold: infra
        ("nginx -v", [], []),
        ("nginx -t -c conf/n.conf", [("read", "/w/conf/n.conf", "")], []),
        ("nginx -s stop", [("process", "nginx", "signal")], []),
        ("terraform version", [], []),
        (
            "terraform destroy -auto-approve",
            [("exec", "terraform-destroy", "remote")],
            ["remote_exec"],
        ),
        ("terraform fmt -check", [("read", "/w", "")], []),
        (
            "terraform init",
            [
                ("exec", "terraform", "package-install"),
                ("network", "registry:terraform", "fetch"),
            ],
            ["package_install"],
        ),
        # gold: pipeline name lookups, one through a planted alias file
        (
            "getent hosts warehouse.internal.invalid",
            [("read", "/etc/hosts", ""), ("network", "warehouse.internal.invalid", "connect")],
            [],
        ),
        ("getent passwd root", [("read", "/etc/passwd", "")], []),
        ("getent shadow", [("read", "/etc/shadow", "")], []),
        # gold: pylib
        ("ruff --version", [], []),
        ("ruff check .", [("read", "/w", "")], []),
        ("ruff check --fix src", [("write", "/w/src", "modify")], []),
        ("ruff format --check", [("read", "/w", "")], []),
        ("pipx list", [], []),
        (
            "pipx install ruff",
            [("exec", "pipx", "package-install"), ("network", "registry:pip", "fetch")],
            ["package_install"],
        ),
        # gold: a recovery-data probe; expire is the destructive form
        ("git reflog -20", [("read", "/w", "")], []),
        ("git reflog expire --expire=now --all", [("delete", "/w/.git/logs", "ref")], []),
        # gold: service migrations
        ('sqlite3 var/orders.sqlite ".schema orders"', [("read", "/w/var/orders.sqlite", "")], []),
        ('sqlite3 var/o.sqlite "SELECT * FROM orders;"', [("read", "/w/var/o.sqlite", "")], []),
        (
            "sqlite3 /scratch/t.sqlite < db/0001_init.sql",
            [("write", "/scratch/t.sqlite", "modify"), ("read", "/w/db/0001_init.sql", "")],
            ["stdin_code"],
        ),
        ('sqlite3 o.db "DROP TABLE orders"', [("write", "/w/o.db", "modify")], []),
        ('sqlite3 o.db "select 1; delete from x"', [("write", "/w/o.db", "modify")], []),
        ('sqlite3 o.db ".shell rm -rf ~"', [("write", "/w/o.db", "modify")], ["interpreter_code"]),
        ('sqlite3 -readonly o.db "DELETE FROM x"', [("read", "/w/o.db", "")], []),
        ('sqlite3 :memory: "select 1"', [], []),
        # gold: notes app probing for a database
        ("pg_isready", [("network", "postgres://localhost:5432", "connect")], []),
        (
            'psql "postgres://app:fixture@localhost/notes" -c "select 1"',
            [("network", "postgres://localhost/notes", "connect")],
            [],
        ),
        (
            'psql -h db -d prod -c "DROP TABLE users"',
            [
                ("network", "postgres://db:5432/prod", "connect"),
                ("exec", "postgres://db:5432/prod", "remote"),
            ],
            ["remote_exec"],
        ),
        (
            'psql -c "select 1 \\! rm -rf /"',
            [
                ("network", "postgres://localhost:5432", "connect"),
                ("exec", "postgres://localhost:5432", "remote"),
            ],
            ["remote_exec"],
        ),
    ],
)
def test_deferred_programs(command: str, effects: Fx, opaque: list[str]) -> None:
    assert fx(command) == (effects, opaque)


def test_credentials_never_reach_the_target() -> None:
    effects, _ = fx('psql "postgres://app:s3cret@db.example/x" -c "select 1"')
    assert all("s3cret" not in target for _, target, _ in effects)


@pytest.mark.parametrize(
    ("command", "effects", "opaque"),
    [
        # gold: environment probes before installing tools (12 hand steps were opaque)
        ("python --version", [], []),
        ("python3 -V", [], []),
        ("pip --version", [], []),
        ("node -v", [], []),
        ("ruby --help", [], []),
        # still code: verbose mode, or a script after the flag
        ("python -v", [("exec", "python", "stdin-code")], ["stdin_code"]),
        ("ruby -v x.rb", [("exec", "/w/x.rb", "script")], ["script_exec"]),
        ("python -c 'print(1)' --version", [("exec", "python", "code")], ["interpreter_code"]),
    ],
)
def test_interpreter_info_flags(command: str, effects: Fx, opaque: list[str]) -> None:
    assert fx(command) == (effects, opaque)


@pytest.mark.parametrize(
    ("command", "target"),
    [
        # gold: tests written in a scratch directory (two traces showed /work)
        ("cd /scratch/x && printf hi > raw/t.csv", "/scratch/x/raw/t.csv"),
        ("cd /scratch/x && cat > a.c <<'EOF'\nint x;\nEOF", "/scratch/x/a.c"),
        ("cd /scratch/x; echo hi > a.txt", "/scratch/x/a.txt"),
        # a compound opens the file before its body runs
        ("{ cd /scratch/x; echo hi; } > a.txt", "/w/a.txt"),
        ("(cd /scratch/x && echo hi) > a.txt", "/w/a.txt"),
    ],
)
def test_redirect_after_cd_in_a_list(command: str, target: str) -> None:
    effects, _ = fx(command)
    assert ("write", target) in [(kind, path) for kind, path, _ in effects]
