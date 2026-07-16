# 로깅 시스템 가이드

## 현재 로그 형식

### 콘솔 출력 (터미널)
```
10.28  07:53:30 |    INFO     | BotCacheManager 초기화 완료 (TTL 제거)
```

**형식**: `날짜  시간 | 레벨 | 메시지`

### 파일 로그 (`bot.log`)
```
2025-10-28  07:53:30 | INFO     | mastodon_bot | __init__:213 | BotCacheManager 초기화 완료 (TTL 제거)
```

**형식**: `날짜 시간 | 레벨 | 로거이름 | 함수:라인 | 메시지`

---

## 로그 레벨별 표시

### INFO (파란색)
```
10.28  07:53:30 |    INFO     | 마스토돈 봇 로깅 시스템 초기화 완료
```
- 일반 정보 메시지
- 성공한 작업
- 시스템 상태

### WARNING (노란색 + ⚠️)
```
10.28  07:53:30 | ⚠️  WARNING | 경고 메시지
```
- 비정상적이지만 복구 가능
- 권장 사항
- 주의가 필요한 상황

### ERROR (빨간색 + ❌)
```
10.28  07:53:30 |  ❌  ERROR | 오류 발생: ...
```
- 처리 실패
- 예외 발생
- 복구 불가능한 오류

### DEBUG (회색 - 일반 모드에서는 숨김)
```
10.28  07:53:30 | 🔍   DEBUG | 디버그 정보
```
- 개발용 상세 정보
- DEBUG_MODE=True일 때만 표시

---

## 로그 형식 변경 방법

### 1. 로그 레벨 변경
`.env` 파일에서:
```env
LOG_LEVEL=INFO    # ERROR, WARNING, INFO, DEBUG
```

### 2. 디버그 모드 활성화
```env
DEBUG_MODE=True   # 상세한 디버그 로그 표시
```

### 3. 콘솔 로그 비활성화
```env
ENABLE_CONSOLE_LOG=False   # 콘솔 출력 숨김
```

---

## 로깅 활용 예시

### 명령어에서 로그 사용
```python
from utils.imports import *

@register_command(name="테스트")
class TestCommand(BaseCommand):
    
    @handle_command_errors
    @log_execution  # ← 자동으로 실행 로그 기록
    def execute(self, context: CommandContext) -> CommandResponse:
        logger.info("명령어 시작")
        logger.warning("주의 사항")
        logger.error("오류 발생")
        logger.debug("디버그 정보")
        
        return CommandResponse.create_success("완료")
```

### 로그 메시지 예시
```python
# 정보 로그
logger.info("처리 완료")

# 경고 로그
logger.warning("잔액이 부족합니다.")

# 에러 로그
logger.error(f"오류 발생: {e}", exc_info=True)

# 디버그 로그
logger.debug(f"인벤토리: {inventory}")
```

---

## 명령어 카테고리별 로그 접두사 규칙

복잡한 명령어(여러 파일로 나뉜 경우)는 `[카테고리]` 접두사를 로그 메시지 앞에 붙여 필터링을 편하게 합니다.

```python
# 단일 파일 명령어 — 접두사 선택 사항
logger.debug(f"구매 처리 시작: user_id={context.user_id}")

# 멀티 파일 명령어 — 파일별 접두사 권장
logger.debug(f"[홀덤] 베팅 처리: player={player_id}")
logger.debug(f"[홀덤 엔진] 페이즈 전환: PREFLOP→FLOP")
logger.debug(f"[홀덤 타이머] 타임아웃 발생: player={player_id}")
logger.debug(f"[홀덤 팟] 사이드팟 계산: {pots}")
```

**접두사 패턴**: `[기능명]` → `[기능명 서브모듈]`
예: `[홀덤]`, `[홀덤 엔진]`, `[홀덤 타이머]`, `[홀덤 팟]`

스레드 추적이 필요한 경우 `status_id` 등을 끝에 포함:
```python
logger.debug(f"[홀덤] 게임 시작: root_status_id={self.root_status_id}")
```

---

## 현재 로그 상태

### 실제 로그 예시
```
2025-10-28  07:53:30 | INFO     | ==================================================
2025-10-28  07:53:30 | INFO     | 마스토돈 봇 로깅 시스템 초기화 완료
2025-10-28  07:53:30 | INFO     | 로그 레벨: INFO
2025-10-28  07:53:30 | INFO     | 파일 로깅: bot.log
2025-10-28  07:53:30 | INFO     | 콘솔 로깅: True
2025-10-28  07:53:30 | INFO     | 디버그 모드: False
2025-10-28  07:53:30 | INFO     | ==================================================
2025-10-28  07:53:30 | INFO     | BotCacheManager 초기화 완료 (TTL 제거)
2025-10-28  07:53:30 | INFO     | 로깅 시스템 종료됨
```

### 주요 특징
- ✅ KST (한국 시간) 형식
- ✅ 색상 구분 (콘솔)
- ✅ 이모지로 레벨 표시
- ✅ 날짜/시간 자동 추가
- ✅ 파일 로그 자동 회전

### 로그 파일 관리
```bash
# 최근 로그 확인
tail -20 bot.log

# 특정 에러만 확인
grep "ERROR" bot.log

# 실시간 로그 확인
tail -f bot.log
```

