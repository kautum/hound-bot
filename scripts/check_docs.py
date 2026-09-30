#!/usr/bin/env python3
"""Docs drift checker: backticked repo paths must exist; check-markers must match the repo.

Exit codes: 0 no drift, 1 drift found, 2 could not determine the truth.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

DOC_FILES = (
    "README.md",
    "PROJECT-WIKI.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "RUNBOOK.md",
    "DEMO-SHOTLIST.md",
)
PATH_EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".toml", ".ini", ".sh", ".mp4", ".gif")
FORBIDDEN_CHARS = set("<>*{}~$")
# Planned artifacts that are documented before they exist.
ALLOWLIST = frozenset({"demo.mp4", "demo.gif"})
IGNORED_TOKENS = frozenset({".env"})
REQUIRED_MARKERS = {
    "README.md": ("test-count",),
    "PROJECT-WIKI.md": ("test-count", "migration-head"),
}
DUMMY_DATABASE_URL = "postgresql+asyncpg://user@localhost/swa_test"
DOT_WORD_RE = re.compile(r"^\.[A-Za-z0-9_]+$")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
MARKER_RE = re.compile(r"<!--\s*check:([a-z-]+)=(\S+?)\s*-->")
MIGRATION_RE = re.compile(r"^(\d{4})_.*\.py$")
COLLECTED_RE = re.compile(r"(\d+) tests? collected")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class UndeterminedError(Exception):
    """The true value could not be established; never guess."""


def looks_like_path(token: str, root: Path) -> bool:
    """Decide whether a backticked token (already :line-stripped) is a repo path claim."""
    if any(c.isspace() for c in token) or FORBIDDEN_CHARS & set(token):
        return False
    if "://" in token or token.startswith("/"):
        return False
    if DOT_WORD_RE.match(token) or token.startswith("...") or ".." in token:
        return False
    if token.endswith(PATH_EXTENSIONS):
        return True
    # No known extension: only a path if it starts inside the repo's top level.
    return "/" in token and (root / token.split("/", 1)[0]).exists()


def normalise_path(token: str) -> str:
    return re.sub(r"(::\w+|:\d+)$", "", token)


def check_paths(root: Path) -> list[str]:
    findings = []
    for name in DOC_FILES:
        doc = root / name
        if not doc.is_file():
            continue
        for lineno, line in enumerate(doc.read_text().splitlines(), start=1):
            for token in BACKTICK_RE.findall(line):
                path = normalise_path(token)
                if path in ALLOWLIST or path in IGNORED_TOKENS:
                    continue
                if not looks_like_path(path, root):
                    continue
                if not (root / path).exists():
                    findings.append(f"{name}:{lineno}: {path}")
    return findings


def find_markers(root: Path, name: str) -> dict[str, list[tuple[int, str]]]:
    """Return {marker_name: [(lineno, value), ...]} for one doc."""
    found: dict[str, list[tuple[int, str]]] = {}
    doc = root / name
    if not doc.is_file():
        return found
    fence = ""  # the opening fence string while inside a code block
    for lineno, line in enumerate(doc.read_text().splitlines(), start=1):
        fence_match = FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group(1)
            if not fence:
                fence = token
                continue
            if token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        for key, value in MARKER_RE.findall(line):
            found.setdefault(key, []).append((lineno, value))
    return found


def migration_head(root: Path) -> str:
    prefixes = []
    for f in (root / "alembic" / "versions").iterdir():
        match = MIGRATION_RE.match(f.name)
        if match:
            prefixes.append(match.group(1))
    if not prefixes:
        raise UndeterminedError("no migration files matching NNNN_*.py found")
    return max(prefixes)


def parse_collected_count(output: str) -> int:
    match = COLLECTED_RE.search(output)
    if not match:
        tail = "\n".join(output.strip().splitlines()[-10:])
        raise UndeterminedError(f"cannot parse test count; raw tail:\n{tail}")
    return int(match.group(1))


def collect_test_count(root: Path) -> int:
    """Count tests via pytest --collect-only; a non-zero exit means the count is untrustworthy."""
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", DUMMY_DATABASE_URL)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root, env=env, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-10:])
        raise UndeterminedError(
            f"pytest --collect-only exited {result.returncode}; raw tail:\n{tail}"
        )
    return parse_collected_count(output)


def check_markers(root: Path, test_count: int, head: str) -> list[str]:
    truth = {"test-count": str(test_count), "migration-head": head}
    findings = []
    for name in DOC_FILES:
        markers = find_markers(root, name)
        for required in REQUIRED_MARKERS.get(name, ()):
            if required not in markers:
                findings.append(f"{name}: missing required marker check:{required}")
        for key, entries in markers.items():
            for lineno, value in entries:
                if key not in truth:
                    findings.append(f"{name}:{lineno}: unknown marker check:{key}")
                elif value != truth[key]:
                    findings.append(
                        f"{name}:{lineno}: check:{key}={value} but actual is {truth[key]}"
                    )
    return findings


def run_checks(root: Path, test_count: int, head: str) -> list[str]:
    return check_paths(root) + check_markers(root, test_count, head)


def main(root: Path | None = None, count_fn=collect_test_count) -> int:
    if root is None:
        root = Path(__file__).resolve().parent.parent
    try:
        findings = run_checks(root, count_fn(root), migration_head(root))
    except UndeterminedError as exc:
        print(f"ERROR: {exc}")
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} docs drift finding(s)")
        return 1
    print("docs OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
