# 마스토돈 스토리 스크립트 자동 출력 봇

Google Sheets의 스토리 스크립트를 자동으로 마스토돈에 송출하는 봇입니다.

## 📋 주요 기능

- **자동 스크립트 송출**: 워크시트의 스크립트를 순차적으로 자동 송출
- **다중 계정 지원**: 6개 마스토돈 계정 (NOTICE, SUBWAY, STORY, WHISPER, STATION, ALEXEY) 관리
- **명령어 기반 트리거**: Direct 메시지로 스토리 진행 명령어 수신
- **워크시트별 실행**: Google Sheets의 각 워크시트별로 개별 스토리 세션 실행
- **실시간 모니터링**: 알림 실시간 처리 및 세션 상태 관리

## 🚀 설치 및 설정

### 1. 필수 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env.example` 파일을 `.env`로 복사하고 실제 값으로 수정:

```bash
cp .env.example .env
```

### 3. Google Sheets 인증

1. Google Cloud Console에서 서비스 계정 생성
2. JSON 키 파일을 `credentials.json`으로 저장
3. Google Sheets API 활성화
4. 스프레드시트를 서비스 계정과 공유

### 4. 마스토돈 계정 설정

각 계정에 대해 마스토돈 애플리케이션 생성 후 토큰 발급:
- 마스토돈 인스턴스 > 설정 > 개발 > 새 애플리케이션

## 📊 워크시트 구조

각 워크시트는 다음 3개 열을 반드시 포함해야 합니다:

| 계정 | 간격 | 문구 |
|------|------|------|
| NOTICE | 30 | 첫 번째 스크립트입니다 |
| SUBWAY | 60 | 두 번째 스크립트입니다 |
| STORY | 45 | 세 번째 스크립트입니다 |

- **계정**: NOTICE, SUBWAY, STORY, WHISPER, STATION, ALEXEY 중 하나
- **간격**: 다음 스크립트까지의 대기 시간 (초 단위)
- **문구**: 송출할 스크립트 내용

## 🎮 사용법

### 봇 실행

```bash
# 기본 실행 (포그라운드)
python main.py

# 백그라운드 실행
python main.py --mode background

# 테스트 모드
python main.py --test

# 상태 확인
python main.py --status
```

### 명령어 사용

1. **NOTICE** 계정에서 **STORY** 계정으로 **Direct 메시지** 전송
2. 지원하는 명령어 형식:
   - `[스토리/워크시트명]`
   - `[스진/워크시트명]`
   - `[스토리진행/워크시트명]`

**예시:**
```
[스토리/테스트시트]
[스진/에피소드1]
[스토리진행/시나리오A]
```

### 동작 과정

1. 명령어 수신 시 해당 워크시트의 모든 스크립트를 캐시로 로드
2. 첫 번째 스크립트부터 순차적으로 송출
3. 각 스크립트의 '간격' 설정에 따라 대기
4. 지정된 '계정'으로 'unlisted' 가시성으로 툿 포스팅
5. 모든 스크립트 송출 완료 후 캐시 삭제

## 🔧 설정 옵션

주요 환경 변수:

```env
# 마스토돈 설정
MASTODON_INSTANCE_URL=https://your-instance.com
STORY_ACCESS_TOKEN=your_story_token
NOTICE_ACCESS_TOKEN=your_notice_token
# ... 기타 계정 토큰들

# Google Sheets 설정
GOOGLE_SHEETS_ID=your_sheets_id

# 시스템 설정
TIMEZONE=Asia/Seoul
LOG_LEVEL=INFO
ERROR_NOTIFICATION_ENABLED=true
```

## 📁 프로젝트 구조

```
autostory/
├── main.py                    # 메인 실행 파일
├── config/
│   └── settings.py           # 설정 관리
├── core/
│   ├── sheets_client.py      # Google Sheets 클라이언트
│   ├── mastodon_client.py    # 마스토돈 API 클라이언트
│   ├── story_loop_manager.py # 스토리 루프 관리자
│   └── notification_handler.py # 알림 처리기
├── utils/
│   ├── datetime_utils.py     # 시간 유틸리티
│   ├── logging_config.py     # 로깅 설정
│   └── validators.py         # 검증 유틸리티
├── logs/                     # 로그 파일 디렉토리
├── credentials.json          # Google 서비스 계정 키
├── .env                      # 환경 변수 설정
└── requirements.txt          # 필수 패키지 목록
```

## 🔍 로그 및 모니터링

- 모든 활동은 `logs/` 디렉토리에 기록
- 실시간 상태는 `--status` 옵션으로 확인
- 오류 발생 시 관리자 계정으로 DM 알림

## ⚠️ 주의사항

1. **캐시 최적화**: 워크시트 전체를 한 번에 로드하여 API 호출 최소화
2. **가시성 설정**: 모든 툿은 `unlisted`로 송출
3. **계정별 송출**: 각 스크립트의 '계정' 열에 지정된 계정으로 송출
4. **Direct 메시지만**: NOTICE→STORY 계정 간 Direct 메시지만 처리
5. **세션 관리**: 한 번에 하나의 워크시트만 진행 가능

## 🐛 문제 해결

### 일반적인 문제

1. **인증 실패**
   - `credentials.json` 파일 확인
   - 서비스 계정 권한 확인

2. **마스토돈 연결 실패**
   - 액세스 토큰 확인
   - 인스턴스 URL 확인

3. **명령어 인식 안됨**
   - Direct 메시지인지 확인
   - NOTICE 계정에서 전송했는지 확인
   - 명령어 형식 확인

4. **워크시트 읽기 실패**
   - 워크시트 이름 확인
   - '계정', '간격', '문구' 열 존재 확인

### 로그 확인

```bash
# 최신 로그 확인
tail -f logs/mastodon_bot_$(date +%Y%m%d).log

# 에러 로그만 확인
grep ERROR logs/mastodon_bot_errors_$(date +%Y%m%d).log
```

## 📞 지원

문제가 발생하면 로그 파일과 함께 문의해주세요.

---

**버전**: 1.0  
**개발일**: 2025.08  
**환경**: Python 3.8+