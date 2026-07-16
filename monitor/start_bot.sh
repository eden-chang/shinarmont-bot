#!/bin/bash

# 마스토돈 + Google Sheets 자동봇 시작 스크립트

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_PID_FILE="$BOT_DIR/bot.pid"
BOT_LOG_FILE="$BOT_DIR/bot.log"

cd "$BOT_DIR"

# 함수 정의
start_bot() {
    if [ -f "$BOT_PID_FILE" ]; then
        PID=$(cat "$BOT_PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "봇이 이미 실행 중입니다 (PID: $PID)"
            return 1
        else
            echo "이전 PID 파일 제거"
            rm -f "$BOT_PID_FILE"
        fi
    fi
    
    echo "봇 시작 중..."
    nohup python3 main.py > "$BOT_LOG_FILE" 2>&1 &
    BOT_PID=$!
    echo $BOT_PID > "$BOT_PID_FILE"
    echo "봇이 시작되었습니다 (PID: $BOT_PID)"
    echo "로그: tail -f $BOT_LOG_FILE"
}

stop_bot() {
    if [ ! -f "$BOT_PID_FILE" ]; then
        echo "봇이 실행되지 않습니다"
        return 1
    fi
    
    PID=$(cat "$BOT_PID_FILE")
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "봇 프로세스를 찾을 수 없습니다"
        rm -f "$BOT_PID_FILE"
        return 1
    fi
    
    echo "봇 종료 중... (PID: $PID)"
    kill -TERM "$PID"
    
    # 정상 종료 대기 (최대 30초)
    for i in {1..30}; do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "봇이 정상적으로 종료되었습니다"
            rm -f "$BOT_PID_FILE"
            return 0
        fi
        sleep 1
    done
    
    echo "강제 종료 시도..."
    kill -KILL "$PID" 2>/dev/null
    rm -f "$BOT_PID_FILE"
    echo "봇이 강제 종료되었습니다"
}

restart_bot() {
    echo "봇 재시작 중..."
    stop_bot
    sleep 2
    start_bot
}

reload_config() {
    if [ ! -f "$BOT_PID_FILE" ]; then
        echo "봇이 실행되지 않습니다"
        return 1
    fi
    
    PID=$(cat "$BOT_PID_FILE")
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "봇 프로세스를 찾을 수 없습니다"
        rm -f "$BOT_PID_FILE"
        return 1
    fi
    
    echo "설정 리로드 중... (PID: $PID)"
    kill -USR1 "$PID"
    echo "설정 리로드 신호를 전송했습니다"
}

status_bot() {
    if [ ! -f "$BOT_PID_FILE" ]; then
        echo "상태: 중지됨"
        return 1
    fi
    
    PID=$(cat "$BOT_PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "상태: 실행 중 (PID: $PID)"
        
        # 상태 파일이 있으면 추가 정보 표시
        if [ -f "bot_status.json" ]; then
            echo "=== 봇 상태 정보 ==="
            python3 -c "
import json
try:
    with open('bot_status.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f'상태: {data.get(\"status\", \"알 수 없음\")}')
    print(f'연속 실패: {data.get(\"consecutive_failures\", 0)}회')
    print(f'재시작 횟수: {data.get(\"restart_count\", 0)}회')
    
    uptime = data.get('uptime', 0)
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    print(f'업타임: {hours}시간 {minutes}분')
except Exception as e:
    print(f'상태 정보를 읽을 수 없습니다: {e}')
"
        fi
        return 0
    else
        echo "상태: 중지됨 (PID 파일은 존재하나 프로세스 없음)"
        rm -f "$BOT_PID_FILE"
        return 1
    fi
}

# 명령어 처리
case "$1" in
    start)
        start_bot
        ;;
    stop)
        stop_bot
        ;;
    restart)
        restart_bot
        ;;
    reload)
        reload_config
        ;;
    status)
        status_bot
        ;;
    logs)
        if [ -f "$BOT_LOG_FILE" ]; then
            tail -f "$BOT_LOG_FILE"
        else
            echo "로그 파일을 찾을 수 없습니다: $BOT_LOG_FILE"
        fi
        ;;
    *)
        echo "사용법: $0 {start|stop|restart|reload|status|logs}"
        echo ""
        echo "명령어:"
        echo "  start   - 봇 시작"
        echo "  stop    - 봇 종료"
        echo "  restart - 봇 재시작"
        echo "  reload  - 설정 리로드 (봇을 재시작하지 않고 설정만 다시 로드)"
        echo "  status  - 봇 상태 확인"
        echo "  logs    - 실시간 로그 보기"
        exit 1
        ;;
esac