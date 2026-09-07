"""TDD for the AI-report workflow's artifact delivery path.

Ref: docs/REVIEW-codex-r4-20260907.md R4-3 — `git add -u artifacts` (`-u` =
update tracked files only) can never stage a brand-new file. New order
artifacts under artifacts/mr20/ (not gitignored, unlike top-level
artifacts/*.json — see .gitignore) were silently never delivered by the
workflow's commit step because of this. The commit steps must explicitly
list a delivery path for new order files instead of relying solely on `-u`.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "update_ai_report.yml"


def _commit_step_runs() -> list[str]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    runs = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step and "git add -u artifacts" in step["run"]:
                runs.append(step["run"])
    return runs


def test_commit_steps_using_git_add_dash_u_artifacts_exist():
    # Sanity check the parse itself finds the steps this test is about.
    assert _commit_step_runs()


def test_commit_steps_explicitly_list_new_orders_delivery_path():
    for run in _commit_step_runs():
        assert "git add artifacts/mr20" in run, (
            "commit step relies solely on `git add -u artifacts` (update-only, "
            "misses brand-new order files) without an explicit delivery path "
            f"for artifacts/mr20:\n{run}"
        )
