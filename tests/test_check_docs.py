"""Tests for scripts/check_docs.py. Counts are passed in; pytest is never run inside pytest."""

import importlib.util
import subprocess
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


FENCED = "```\n<!-- check:test-count=5 -->\n```\n"


def test_marker_only_inside_fence_counts_as_missing(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "README.md", FENCED)
    assert check_docs.find_markers(tmp_path, "README.md") == {}
    findings = check_docs.check_markers(tmp_path, 5, "0002")
    assert findings == ["README.md: missing required marker check:test-count"]


def test_tilde_and_longer_fences_hide_markers_and_lines_stay_correct(tmp_path):
    make_repo(tmp_path)
    text = (
        "~~~\n<!-- check:test-count=1 -->\n~~~\n"
        "````\n```\n<!-- check:test-count=2 -->\n````\n"
        "<!-- check:test-count=5 -->\n"
    )
    write(tmp_path, "README.md", text)
    assert check_docs.find_markers(tmp_path, "README.md") == {"test-count": [(8, "5")]}


def test_marker_after_closed_fence_still_found(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "README.md", FENCED + "<!-- check:test-count=7 -->\n")
    assert check_docs.find_markers(tmp_path, "README.md") == {"test-count": [(4, "7")]}


def test_duplicate_marker_with_different_value_flagged(tmp_path):
    make_repo(tmp_path)
    write(tmp_path, "README.md", "<!-- check:test-count=5 -->\n<!-- check:test-count=9 -->\n")
    findings = check_docs.check_markers(tmp_path, 5, "0002")
    assert findings == ["README.md:2: check:test-count=9 but actual is 5"]


def fake_run(returncode, stdout, stderr=""):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_collect_nonzero_returncode_is_undetermined_even_with_count(tmp_path, monkeypatch):
    out = "203 tests collected, 1 error\nERROR tests/test_x.py"
    monkeypatch.setattr(check_docs.subprocess, "run", fake_run(2, out))
    with pytest.raises(check_docs.UndeterminedError, match="raw tail") as info:
        check_docs.collect_test_count(tmp_path)
    assert "ERROR tests/test_x.py" in str(info.value)


def test_collect_zero_returncode_returns_count(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs.subprocess, "run", fake_run(0, "5 tests collected in 0.1s"))
    assert check_docs.collect_test_count(tmp_path) == 5


def test_main_exit_0_when_everything_matches(tmp_path, capsys):
    make_repo(tmp_path)
    assert check_docs.main(tmp_path, count_fn=lambda root: 5) == 0
    assert "docs OK" in capsys.readouterr().out


def test_main_exit_1_on_missing_path(tmp_path, capsys):
    make_repo(tmp_path)
    write(tmp_path, "RUNBOOK.md", "`app/nope.py`\n")
    assert check_docs.main(tmp_path, count_fn=lambda root: 5) == 1
    assert "RUNBOOK.md:1: app/nope.py" in capsys.readouterr().out


def test_main_exit_1_on_wrong_marker(tmp_path, capsys):
    make_repo(tmp_path)
    assert check_docs.main(tmp_path, count_fn=lambda root: 6) == 1
    assert "actual is 6" in capsys.readouterr().out


def test_main_exit_1_on_fenced_only_marker(tmp_path, capsys):
    make_repo(tmp_path)
    write(tmp_path, "README.md", FENCED)
    assert check_docs.main(tmp_path, count_fn=lambda root: 5) == 1
    assert "missing required marker" in capsys.readouterr().out


def test_main_exit_2_on_undetermined(tmp_path, capsys):
    make_repo(tmp_path)

    def boom(root):
        raise check_docs.UndeterminedError("no count")

    assert check_docs.main(tmp_path, count_fn=boom) == 2
    assert "ERROR: no count" in capsys.readouterr().out
