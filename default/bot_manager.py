"""
다중 봇 관리기.

환경 설정을 기반으로 여러 개의 봇 프로세스를 실행/감시한다.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, List, Optional

import os

from config.bot_config import BotConfig, discover_bot_slots, load_bot_configs_from_env
from utils.admin_notifier import AdminNotifier  # type: ignore[import]

try:  # pragma: no cover - psutil 미설치 환경 대비
    from utils.process_health import ProcessHealthChecker  # type: ignore[import]
except Exception:  # pylint: disable=broad-except
    ProcessHealthChecker = None  # type: ignore[assignment]

from bot_process_wrapper import BotProcess, BotStatus, create_bot_process

# utils.logging_config 사용 (싱글 봇과 동일한 로깅)
try:
    from utils.logging_config import setup_logging, logger as base_logger
    # BotManager용 로거 생성 (setup_logging이 설정한 핸들러 사용)
    LOGGER = logging.getLogger("bot_manager")
    # 로깅 시스템이 이미 초기화되지 않았다면 초기화
    if not logging.getLogger().handlers:
        setup_logging()
except ImportError:
    # 폴백: 기본 로깅 설정
    LOGGER = logging.getLogger("bot_manager")
    PROJECT_ROOT = Path(__file__).resolve().parent
    LOG_PATH = PROJECT_ROOT / "logs" / "manager.log"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


class LogStreamReader:
    """
    봇 프로세스의 stdout을 읽어서 메인 콘솔에 출력하는 스레드
    """
    def __init__(self, bot_name: str, stream, output_queue: Queue):
        """
        Args:
            bot_name: 봇 이름 (로그 prefix용)
            stream: 읽을 스트림 (subprocess.stdout)
            output_queue: 로그 메시지를 넣을 큐
        """
        self.bot_name = bot_name
        self.stream = stream
        self.output_queue = output_queue
        self.running = False
        self.thread = None

    def start(self):
        """스레드 시작"""
        self.running = True
        self.thread = threading.Thread(
            target=self._read_stream,
            daemon=True,
            name=f"LogReader-{self.bot_name}"
        )
        self.thread.start()

    def _read_stream(self):
        """스트림을 읽어서 큐에 넣는 메서드"""
        try:
            for line in iter(self.stream.readline, ''):
                if not self.running:
                    break
                if line:
                    # 줄바꿈 제거
                    line = line.rstrip('\n\r')
                    # 큐에 (봇이름, 로그라인) 튜플로 추가
                    self.output_queue.put((self.bot_name, line))
        except Exception as exc:
            LOGGER.debug("LogStreamReader 예외 (%s): %s", self.bot_name, exc)
        finally:
            try:
                self.stream.close()
            except Exception:
                pass
            LOGGER.debug("LogStreamReader 종료 (%s)", self.bot_name)

    def stop(self):
        """스레드 정지"""
        self.running = False
        try:
            self.stream.close()
        except Exception:
            pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)


def _env_to_bool(value: str, default: bool = False) -> bool:
    """문자열 환경 변수를 bool로 변환."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class BotManager:
    """여러 봇 프로세스를 관리하는 클래스."""

    def __init__(self, env_file_path: str = ".env", notification_api=None):
        self.env_file_path = env_file_path
        self.bot_processes: Dict[str, BotProcess] = {}
        self.running: bool = False
        self.start_time: Optional[float] = None

        # 로그 출력을 위한 큐와 스레드
        self.log_queue: Queue = Queue()
        self.log_readers: Dict[str, LogStreamReader] = {}
        self.log_printer_thread: Optional[threading.Thread] = None

        self.auto_restart = _env_to_bool(
            self._get_env("AUTO_RESTART_ON_CRASH", fallback="True"), True
        )
        self.restart_delay = int(self._get_env("RESTART_DELAY_SECONDS", fallback="5"))
        self.max_restart_attempts = int(
            self._get_env("MAX_RESTART_ATTEMPTS", fallback="3")
        )
        self.healthcheck_interval = int(
            self._get_env("HEALTHCHECK_INTERVAL_SECONDS", fallback="60")
        )
        self.monitor_enabled = _env_to_bool(
            self._get_env("PROCESS_MONITOR_ENABLED", fallback="True"), True
        )

        self.notifier = AdminNotifier(
            mastodon_api=notification_api,
            admin_id=self._get_env("SYSTEM_ADMIN_ID", fallback=""),
            enabled=_env_to_bool(self._get_env("ENABLE_ADMIN_ALERTS", fallback="True")),
        )

        if ProcessHealthChecker is not None:
            try:
                self.health_checker = ProcessHealthChecker()
            except Exception:  # pragma: no cover - psutil optional
                self.health_checker = None
                LOGGER.info("ProcessHealthChecker 사용 불가 (선택 사항)")
        else:
            self.health_checker = None
            LOGGER.info("psutil 미설치로 헬스체크 기능 비활성화")

        self._stop_event = threading.Event()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ------------------------------------------------------------------
    # 환경 로딩
    # ------------------------------------------------------------------
    def _get_env(self, key: str, fallback: Optional[str] = None) -> Optional[str]:
        """env 파일 또는 현재 환경에서 설정 값을 읽는다."""
        value = os.environ.get(key)
        if value is not None:
            return value

        env_path = Path(self.env_file_path)
        if not env_path.exists():
            return fallback

        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, env_value = line.split("=", 1)
            if env_key.strip() == key:
                return env_value.strip().strip('"').strip("'")
        return fallback

    def _load_configs_from_process_env(self) -> List[BotConfig]:
        """현재 프로세스 환경 변수에서 봇 설정을 읽는다.

        BOT_COUNT와 무관하게 환경변수에 존재하는 BOTn_* 슬롯을 자동 탐지한 뒤,
        ENABLED=True인 슬롯만 로드한다. 슬롯 번호가 연속되지 않아도 된다.
        """
        enable_multi_bot = _env_to_bool(os.environ.get("ENABLE_MULTI_BOT", "False"))
        if not enable_multi_bot:
            return []

        slot_numbers = discover_bot_slots(os.environ)
        configs: List[BotConfig] = []
        for idx in slot_numbers:
            bot_id = f"BOT{idx}"
            config = BotConfig.from_env(bot_id, os.environ)  # type: ignore[arg-type]  # noqa: PGH003
            if not config.enabled:
                continue

            is_valid, errors = config.validate()
            if not is_valid:
                LOGGER.error("봇 설정 검증 실패 (%s): %s", config.bot_name, errors)
                continue
            configs.append(config)
        return configs

    # ------------------------------------------------------------------
    # 설정 로드
    # ------------------------------------------------------------------
    def load_bot_configs(self) -> List[BotConfig]:
        """봇 설정을 로드한다."""
        env_path = Path(self.env_file_path)
        if env_path.exists():
            LOGGER.info("환경 파일에서 봇 설정 로드: %s", env_path)
            configs = load_bot_configs_from_env(str(env_path))
        else:
            LOGGER.info("환경 파일 미존재. 현재 환경 변수에서 봇 설정 로드.")
            configs = self._load_configs_from_process_env()

        LOGGER.info("활성화된 봇 %d개 발견", len(configs))
        for cfg in configs:
            LOGGER.info("  - %s (명령어 %d개)", cfg.bot_name, len(cfg.command_filter))
        return configs

    # ------------------------------------------------------------------
    # 실행/정지
    # ------------------------------------------------------------------
    def start_all_bots(self) -> bool:
        """등록된 모든 봇을 실행한다."""
        configs = self.load_bot_configs()
        if not configs:
            LOGGER.error("실행할 봇이 없습니다.")
            return False

        success_count = 0
        for config in configs:
            try:
                bot_process = create_bot_process(config)
                bot_process.start()
                self.bot_processes[config.bot_name] = bot_process

                # 각 봇의 stdout을 읽는 스레드 시작
                if bot_process.process and bot_process.process.stdout:
                    reader = LogStreamReader(
                        config.bot_name,
                        bot_process.process.stdout,
                        self.log_queue
                    )
                    reader.start()
                    self.log_readers[config.bot_name] = reader

                success_count += 1
                LOGGER.info("봇 시작: %s (PID=%s)", config.bot_name, bot_process.process.pid)
            except Exception as exc:  # pragma: no cover - 프로세스 실행 에러
                LOGGER.exception("봇 시작 실패 (%s): %s", config.bot_name, exc)

        if success_count == 0:
            LOGGER.error("봇 프로세스를 하나도 실행하지 못했습니다.")
            return False

        self.running = True
        self.start_time = time.time()

        # 로그 출력 스레드 시작
        self._start_log_printer()

        LOGGER.info("총 %d/%d 개 봇 실행", success_count, len(configs))
        try:
            self.notifier.send_manager_start_alert(success_count)
        except Exception:  # pragma: no cover - 알림 실패는 치명적 아님
            LOGGER.debug("매니저 시작 알림 전송 실패", exc_info=True)
        return True

    def stop_all_bots(self, graceful: bool = True, timeout: int = 15) -> None:
        """모든 봇을 종료한다."""
        self.running = False
        self._stop_event.set()

        # 로그 리더 스레드 정지
        for name, reader in list(self.log_readers.items()):
            reader.stop()
        self.log_readers.clear()

        # 봇 프로세스 종료
        for name, process in list(self.bot_processes.items()):
            LOGGER.info("봇 종료 중: %s", name)
            try:
                if graceful:
                    process.stop(timeout=timeout)
                else:
                    process.kill()
            finally:
                self.bot_processes.pop(name, None)
        try:
            self.notifier.send_manager_stop_alert()
        except Exception:  # pragma: no cover
            LOGGER.debug("매니저 종료 알림 전송 실패", exc_info=True)

    def restart_bot(self, bot_name: str) -> bool:
        """특정 봇을 재시작한다."""
        process = self.bot_processes.get(bot_name)
        if not process:
            LOGGER.error("재시작 대상 봇을 찾을 수 없습니다: %s", bot_name)
            return False

        # 기존 로그 리더 정지
        if bot_name in self.log_readers:
            self.log_readers[bot_name].stop()
            del self.log_readers[bot_name]

        # 봇 프로세스 재시작
        process.stop(timeout=5)
        time.sleep(self.restart_delay)
        if process.start():
            process.restart_count += 1

            # 새로운 로그 리더 시작
            if process.process and process.process.stdout:
                reader = LogStreamReader(
                    bot_name,
                    process.process.stdout,
                    self.log_queue
                )
                reader.start()
                self.log_readers[bot_name] = reader

            return True
        process.status = BotStatus.FAILED
        return False

    def _start_log_printer(self):
        """로그 출력 스레드 시작"""
        def print_logs():
            """큐에서 로그를 읽어서 콘솔에 출력"""
            while self.running:
                try:
                    # 0.1초마다 큐 확인
                    bot_name, log_line = self.log_queue.get(timeout=0.1)

                    # 봇 이름 prefix 추가하여 출력
                    # ANSI 색상 코드를 유지하면서 prefix만 추가
                    prefix = f"\033[90m[{bot_name}]\033[0m "
                    print(f"{prefix}{log_line}", flush=True)

                except Empty:
                    continue
                except Exception as exc:
                    LOGGER.debug("로그 출력 오류: %s", exc)

        self.log_printer_thread = threading.Thread(
            target=print_logs,
            daemon=True,
            name="LogPrinter"
        )
        self.log_printer_thread.start()

    # ------------------------------------------------------------------
    # 모니터링
    # ------------------------------------------------------------------
    def monitor_loop(self) -> None:
        """봇 프로세스를 주기적으로 점검한다."""
        if not self.monitor_enabled:
            while self.running:
                self._stop_event.wait(timeout=1)
            return

        last_check = 0.0
        while self.running:
            self._stop_event.wait(timeout=1)
            now = time.time()
            if now - last_check < self.healthcheck_interval:
                continue
            last_check = now
            for name, process in list(self.bot_processes.items()):
                if not process.is_alive():
                    LOGGER.error("봇 프로세스 종료 감지: %s", name)
                    process.status = BotStatus.CRASHED
                    if self.auto_restart:
                        self._handle_crash(process)
                elif self.health_checker:
                    try:
                        result = self.health_checker.check_process(process.process.pid)  # type: ignore[arg-type]
                        if result.needs_restart():
                            LOGGER.critical("헬스체크 실패: %s (%s)", name, result.message)
                            process.status = BotStatus.CRASHED
                            if self.auto_restart:
                                self._handle_crash(process)
                        elif not result.is_healthy():
                            LOGGER.warning("헬스체크 경고: %s (%s)", name, result.message)
                    except Exception as exc:  # pragma: no cover - psutil optional
                        LOGGER.debug("헬스체크 예외 무시: %s", exc)

    def _handle_crash(self, process: BotProcess) -> None:
        """프로세스 비정상 종료 처리."""
        bot_name = process.config.bot_name
        if process.restart_count >= self.max_restart_attempts:
            LOGGER.critical("재시작 한계를 초과했습니다: %s", bot_name)
            process.status = BotStatus.FAILED
            # 로그 리더 정지
            if bot_name in self.log_readers:
                self.log_readers[bot_name].stop()
                del self.log_readers[bot_name]
            self.notifier.send_bot_crash_alert(
                bot_name=bot_name,
                bot_id=process.config.bot_id,
                message="재시작 한계 초과",
                restart_count=process.restart_count,
                uptime_seconds=process.get_uptime(),
                pid=process.process.pid if process.process else None,
            )
            return

        # 기존 로그 리더 정지
        if bot_name in self.log_readers:
            self.log_readers[bot_name].stop()
            del self.log_readers[bot_name]

        time.sleep(self.restart_delay)
        if process.start():
            process.restart_count += 1

            # 새로운 로그 리더 시작
            if process.process and process.process.stdout:
                reader = LogStreamReader(
                    bot_name,
                    process.process.stdout,
                    self.log_queue
                )
                reader.start()
                self.log_readers[bot_name] = reader

            self.notifier.send_bot_restart_success(
                bot_name=bot_name,
                bot_id=process.config.bot_id,
                restart_count=process.restart_count,
            )
        else:
            process.status = BotStatus.FAILED
            self.notifier.send_bot_crash_alert(
                bot_name=bot_name,
                bot_id=process.config.bot_id,
                message="재시작 실패",
                restart_count=process.restart_count,
                uptime_seconds=process.get_uptime(),
                pid=process.process.pid if process.process else None,
            )

    # ------------------------------------------------------------------
    # 상태 조회
    # ------------------------------------------------------------------
    def get_bot_status(self) -> Dict[str, Dict[str, object]]:
        """각 봇의 상태 정보를 반환."""
        return {name: process.get_status_dict() for name, process in self.bot_processes.items()}

    def get_manager_status(self) -> Dict[str, object]:
        """매니저 상태 정보를 반환."""
        uptime = time.time() - self.start_time if self.start_time else 0
        return {
            "running": self.running,
            "uptime_seconds": uptime,
            "bot_count": len(self.bot_processes),
            "bots": self.get_bot_status(),
            "config": {
                "auto_restart": self.auto_restart,
                "restart_delay": self.restart_delay,
                "max_restart_attempts": self.max_restart_attempts,
                "healthcheck_interval": self.healthcheck_interval,
                "monitor_enabled": self.monitor_enabled,
            },
        }

    def dump_status_json(self) -> str:
        """상태를 JSON 문자열로 반환."""
        return json.dumps(self.get_manager_status(), ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 시그널 핸들러
    # ------------------------------------------------------------------
    def _signal_handler(self, signum, _frame) -> None:  # pragma: no cover - 시스템 시그널
        LOGGER.info("종료 시그널 수신: %s", signum)
        self.running = False
        self.stop_all_bots(graceful=True)


def main() -> int:  # pragma: no cover - CLI 용도
    """간단한 CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="멀티 봇 매니저")
    parser.add_argument("--env", default=".env", help=".env 파일 경로")
    parser.add_argument("--status", action="store_true", help="상태 출력 후 종료")
    parser.add_argument("--start", action="store_true", help="봇 실행")
    parser.add_argument("--stop", action="store_true", help="봇 중지")

    args = parser.parse_args()

    manager = BotManager(env_file_path=args.env)
    if args.status:
        print(manager.dump_status_json())
        return 0

    if args.stop:
        manager.stop_all_bots(graceful=True)
        return 0

    if args.start or not any([args.status, args.stop]):
        if not manager.start_all_bots():
            return 1
        try:
            manager.monitor_loop()
        except KeyboardInterrupt:
            manager.stop_all_bots(graceful=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - 스크립트 실행
    sys.exit(main())

