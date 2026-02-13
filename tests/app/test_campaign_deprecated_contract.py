from __future__ import annotations

from pathlib import Path


def _read_repo_file(relative_path: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / relative_path).read_text(encoding="utf-8")


def test_deprecated_campaign_runner_not_used_in_scheduler_or_admin():
    targets = {
        "app/worker/scheduler_worker.py": [
            "run_campaigns_sync",
            "run_campaigns_with_claim",
            "run_due_campaigns",
            "campaign_runner import process_scheduled_campaigns",
            "campaign_runner import run_campaigns",
            "campaign_runner import run_due_campaigns",
            "campaign_runner import run_campaigns_sync",
        ],
        "app/api/v1/admin.py": [
            "run_campaigns_sync",
            "run_campaigns_with_claim",
            "run_due_campaigns",
            "process_scheduled_campaigns",
            "run_campaigns(",
        ],
        "app/workers/tasks.py": [
            "campaign_runner import process_scheduled_campaigns",
            "run_campaigns_sync",
            "run_campaigns_with_claim",
            "run_due_campaigns",
            "run_campaigns(",
        ],
    }

    for rel_path, forbidden in targets.items():
        content = _read_repo_file(rel_path)
        for token in forbidden:
            assert token not in content, f"Forbidden token {token!r} in {rel_path}"
