"""
봇 서브프로세스 실행/관리 유틸리티.

멀티 봇 모드에서 각 봇을 독립 프로세스로 실행하고 수명 주기를 관리한다.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from config.bot_config import BotConfig

PROJECT_ROOT = Path(__file__).resolve().parent


class BotStatus(Enum):
    """봇 프로세스 실행 상태."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    CRASHED = "crashed"
    FAILED = "failed"


@dataclass
class BotProcess:
    """단일 봇 프로세스를 캡슐화한다."""

    config: BotConfig
    process: Optional[subprocess.Popen] = field(default=None, init=False)
    start_time: Optional[float] = field(default=None, init=False)
    restart_count: int = field(default=0, init=False)
    status: BotStatus = field(default=BotStatus.STOPPED, init=False)

    def start(self) -> bool:
        """봇 프로세스를 실행한다."""
        if self.is_alive():
            return True

        env_vars: Dict[str, str] = os.environ.copy()
        env_vars.update(self.config.to_env_dict())

        python_executable = sys.executable or "python"
        command = [python_executable, "main.py", "--bot-id", self.config.bot_id]

        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        self.status = BotStatus.STARTING
        self.process = subprocess.Popen(  # pylint: disable=R1732
            command,
            cwd=str(PROJECT_ROOT),
            env=env_vars,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            creationflags=creation_flags,
        )

        self.start_time = time.time()
        self.status = BotStatus.RUNNING
        return True

    def stop(self, timeout: int = 10) -> None:
        """프로세스를 정상 종료한다."""
        if not self.process:
            return

        if self.process.poll() is None:
            try:
                if os.name == "nt":
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    self.process.terminate()
            except Exception:
                self.process.kill()

            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

        self._cleanup()

    def kill(self) -> None:
        """프로세스를 강제 종료한다."""
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        self._cleanup()

    def is_alive(self) -> bool:
        """프로세스가 실행 중인지 확인."""
        return bool(self.process and self.process.poll() is None)

    def healthcheck(self) -> bool:
        """기본 헬스체크. 추후 확장을 위해 별도 메서드로 분리."""
        return self.is_alive()

    def get_uptime(self) -> float:
        """프로세스 가동 시간을 반환."""
        if not self.start_time:
            return 0.0
        return time.time() - self.start_time

    def get_status_dict(self) -> Dict[str, object]:
        """상태 정보를 dict로 반환."""
        return {
            "status": self.status.value,
            "pid": self.process.pid if self.process else None,
            "uptime_seconds": self.get_uptime(),
            "restart_count": self.restart_count,
            "command_filter": self.config.command_filter,
        }

    def _cleanup(self) -> None:
        """내부 리소스를 정리."""
        self.status = BotStatus.STOPPED
        self.process = None
        self.start_time = None


def create_bot_process(config: BotConfig) -> BotProcess:
    """BotProcess 생성 헬퍼."""
    return BotProcess(config)

