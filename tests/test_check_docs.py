"""Tests for scripts/check_docs.py. Counts are passed in; pytest is never run inside pytest."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_docs.py"
_spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)

GOOD_MARKERS = "<!-- check:test-count=5 -->\n<!-- check:migration-head=0002 -->\n"


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_repo(root: Path) -> None:
    write(root, "app/core/config.py", "")
    write(root, "alembic/versions/0001_init.py", "")
    write(root, "alembic/versions/0002_more.py", "")
    write(root, "README.md", "<!-- check:test-count=5 -->\n")
    write(root, "PROJECT-WIKI.md", GOOD_MARKERS)


def test_existing_path_passes(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "ARCHITECTURE.md", "see `app/core/config.py` and `app/core/config.py:12`\n")
    assert check_docs.check_paths(tmp_path) == []


def test_missing_path_reported_with_line(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "ARCHITECTURE.md", "ok\nsee `app/nope.py::thing` here\n")
    assert check_docs.check_paths(tmp_path) == ["ARCHITECTURE.md:2: app/nope.py"]


def test_url_route_env_and_allowlist_ignored(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "RUNBOOK.md", "`/slack/events` `.env` `https://x.io/a.py` `demo.mp4` `a b/c`\n")
    assert check_docs.check_paths(tmp_path) == []


def test_build_log_and_live_fire_never_scanned(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "LIVE-FIRE.md", "`missing/file.py`\n")
    write(tmp_path, "docs/BUILD-LOG.md", "`missing/file.py`\n")
    assert check_docs.check_paths(tmp_path) == []


def test_missing_required_marker_fails(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "README.md", "no marker\n")
    write(tmp_path, "PROJECT-WIKI.md", "<!-- check:test-count=5 -->\n")
    findings = check_docs.check_markers(tmp_path, 5, "0002")
    assert "README.md: missing required marker check:test-count" in findings
    assert "PROJECT-WIKI.md: missing required marker check:migration-head" in findings


def test_wrong_marker_value_fails(tmp_path):
    make_repo(tmp_path)
    findings = check_docs.check_markers(tmp_path, 6, "0003")
    assert len(findings) == 3
    assert any("check:test-count=5 but actual is 6" in f for f in findings)
    assert any("check:migration-head=0002 but actual is 0003" in f for f in findings)


def test_correct_markers_pass(tmp_path):
    make_repo(tmp_path)
    assert check_docs.check_markers(tmp_path, 5, "0002") == []


def test_migration_head_picks_highest_and_ignores_others(tmp_path):
    write(tmp_path, "alembic/versions/0003_a.py", "")
    write(tmp_path, "alembic/versions/0010_b.py", "")
    write(tmp_path, "alembic/versions/9999_x.txt", "")
    write(tmp_path, "alembic/versions/__init__.py", "")
    write(tmp_path, "alembic/versions/12_short.py", "")
    assert check_docs.migration_head(tmp_path) == "0010"


def test_migration_head_none_fails_loudly(tmp_path):
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    with pytest.raises(check_docs.UndeterminedError):
        check_docs.migration_head(tmp_path)


def test_unparseable_collect_output_fails_loudly():
    with pytest.raises(check_docs.UndeterminedError, match="raw tail"):
        check_docs.parse_collected_count("boom\nImportError")
    assert check_docs.parse_collected_count("x\n193 tests collected in 0.4s") == 193


def paths_flagged(root: Path, text: str) -> list[str]:
    make_repo(root)
    write(root, "RUNBOOK.md", text)
    return check_docs.check_paths(root)


def test_slash_token_without_extension_needs_existing_top_level(tmp_path):
    findings = paths_flagged(tmp_path, "`openai/gpt-oss-120b` `education.github.com/pack`\n")
    assert findings == []


def test_slash_token_under_existing_top_level_still_flagged(tmp_path):
    findings = paths_flagged(tmp_path, "`app/observability/` `app/core/`\n")
    assert findings == ["RUNBOOK.md:1: app/observability/"]


def test_dot_word_tokens_ignored(tmp_path):
    assert paths_flagged(tmp_path, "`.gif` `.env` `.gitignore` `.a_1`\n") == []


def test_dot_word_rule_does_not_swallow_real_names(tmp_path):
    write(tmp_path, ".hidden/keep.txt", "")
    findings = paths_flagged(tmp_path, "`.hidden/ci.yml` `x.gif`\n")
    assert findings == ["RUNBOOK.md:1: .hidden/ci.yml", "RUNBOOK.md:1: x.gif"]


def test_ellipsis_and_dotdot_tokens_ignored(tmp_path):
    findings = paths_flagged(tmp_path, "`.../auth/calendar.freebusy` `../x/y.py` `app/../z`\n")
    assert findings == []


def test_single_dot_segments_not_treated_as_dotdot(tmp_path):
    assert paths_flagged(tmp_path, "`app/a.b/missing.py`\n") == ["RUNBOOK.md:1: app/a.b/missing.py"]


def test_known_extension_always_checked_regardless_of_first_segment(tmp_path):
    findings = paths_flagged(tmp_path, "`routes_events.py` `agent/tools.py` `core/config.py`\n")
    assert findings == [
        "RUNBOOK.md:1: routes_events.py",
        "RUNBOOK.md:1: agent/tools.py",
        "RUNBOOK.md:1: core/config.py",
    ]


def test_known_extension_existing_file_passes(tmp_path):
    assert paths_flagged(tmp_path, "`app/core/config.py` `README.md`\n") == []
