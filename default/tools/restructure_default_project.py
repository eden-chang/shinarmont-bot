from __future__ import annotations

import os
import shutil
from pathlib import Path


def move(src: Path, dest: Path) -> None:
    """Move file or directory from src to dest, creating parents as needed."""
    if not src.exists():
        print(f"⚠️  경로 없음, 건너뜀: {src}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"➡️  이동: {src} -> {dest}")
    shutil.move(str(src), str(dest))


def remove_if_empty(path: Path) -> None:
    """Remove directory if empty."""
    if path.exists() and path.is_dir():
        try:
            next(path.iterdir())
        except StopIteration:
            print(f"🧹 빈 디렉터리 삭제: {path}")
            path.rmdir()


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    print(f"작업 디렉터리: {root}")

    # 필수 디렉터리 생성
    dirs_to_create = [
        root / "dev" / "scripts",
        root / "dev" / "experiments" / "commands",
        root / "docs" / "guides",
        root / "docs" / "reports",
        root / "docs" / "reference",
    ]

    for directory in dirs_to_create:
        directory.mkdir(parents=True, exist_ok=True)
        print(f"📁 디렉터리 확보: {directory}")

    moves = [
        ("apply_phase4_patch.py", "dev/scripts/apply_phase4_patch.py"),
        ("bot_manager_phase4_patch.py", "dev/scripts/bot_manager_phase4_patch.py"),
        ("bot_manager.py", "dev/scripts/bot_manager.py"),
        ("bot_manager.py.backup", "dev/scripts/bot_manager.py.backup"),
        ("bot_process_wrapper.py", "dev/scripts/bot_process_wrapper.py"),
        ("scripts/new_command.py", "dev/scripts/new_command.py"),
        ("locks_test", "dev/experiments/locks_test"),
        ("commands/default/card_command.py.disabled", "dev/experiments/commands/card_command.py.disabled"),
        ("test_multibot_logic.py", "tests/test_multibot_logic.py"),
        ("PHASE_5_COMPLETION_REPORT.md", "docs/reports/PHASE_5_COMPLETION_REPORT.md"),
        ("PHASE_5_INTEGRATION_GUIDE.md", "docs/guides/PHASE_5_INTEGRATION_GUIDE.md"),
        ("md/MULTI_BOT_IMPLEMENTATION_PLAN.md", "docs/guides/MULTI_BOT_IMPLEMENTATION_PLAN.md"),
        ("md/OPTION_1_COMPLETION_SUMMARY.md", "docs/reports/OPTION_1_COMPLETION_SUMMARY.md"),
        ("md/PHASE_1_3_TEST_REPORT.md", "docs/reports/PHASE_1_3_TEST_REPORT.md"),
        ("md/PHASE_4_COMPLETION_REPORT.md", "docs/reports/PHASE_4_COMPLETION_REPORT.md"),
        ("md/PHASE_4_INTEGRATION_GUIDE.md", "docs/guides/PHASE_4_INTEGRATION_GUIDE.md"),
        ("md/PHASE_4_MANUAL_INTEGRATION.md", "docs/guides/PHASE_4_MANUAL_INTEGRATION.md"),
        ("docs/COMMAND_RULES.md", "docs/reference/COMMAND_RULES.md"),
        ("docs/COMMAND_TEMPLATE.md", "docs/reference/COMMAND_TEMPLATE.md"),
        ("docs/LOGGING_GUIDE.md", "docs/reference/LOGGING_GUIDE.md"),
    ]

    for src_rel, dest_rel in moves:
        src_path = root / src_rel
        dest_path = root / dest_rel
        move(src_path, dest_path)

    # 빈 디렉터리 정리
    remove_if_empty(root / "scripts")
    remove_if_empty(root / "md")


if __name__ == "__main__":
    main()

