# -*- coding: utf-8 -*-
"""이성/건강 경고 문구 '발송 플래그' 리셋 (VM에서 1회 실행).

배경:
  2026-07-27 시작 재전송 스윕 최초 도입 시, DM 전송기 재초기화가 대기 큐를
  버리는 버그로 문구 DM이 실제로는 안 나갔는데 game_state에는 '발송함'으로
  기록됐다. 코드는 고쳤지만(멱등 initialize_dm_sender), 이미 기록된 플래그
  때문에 재시작해도 그 캐릭터들은 skip된다. 이 스크립트로 플래그를 지우면,
  다음 봇 시작 때 '현재값이 임계 이하'인 문구가 다시 발송된다.

무엇을 하나:
  state/ 아래 **모든 슬롯**의 game_state.json을 직접 스캔해, 각 캐릭터에서
  아래 플래그 키를 삭제(존재할 때만).
    이성문구1발송 / 이성문구2발송 / 건강문구1발송 / 건강문구2발송
  (이성/건강 '수치'나 다른 game_state 값은 건드리지 않는다.)

  ※ 멀티봇은 슬롯마다 state/{슬롯}/game_state.json 이 따로다. 실행 봇이 어느
    슬롯이든 놓치지 않도록 모든 파일을 훑는다.

사용:
  python tools/reset_stat_msg_flags.py            # 실제 리셋
  python tools/reset_stat_msg_flags.py --dry-run  # 미리보기(변경 없음)

주의:
  반드시 봇을 '멈춘 상태'에서 실행한다(실행 중이면 다음 저장에 덮여 되돌아간다).
  리셋 후 봇을 재시작하면 시작 스윕이 재전송한다.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

# 플래그 키는 stat_gate에서 그대로 가져와 이름 드리프트를 막는다.
try:
    from utils.stat_gate import STAT_MSG_TIERS
    FLAG_KEYS = [tier[4] for tier in STAT_MSG_TIERS]
except Exception:
    FLAG_KEYS = ['이성문구1발송', '이성문구2발송', '건강문구1발송', '건강문구2발송']


def _find_state_files():
    """state/ 아래 모든 game_state.json 경로."""
    state_dir = BASE / 'state'
    if not state_dir.exists():
        return []
    # state/game_state.json 및 state/*/game_state.json 모두 포함
    return sorted(set(state_dir.rglob('game_state.json')))


def _reset_file(path: Path, dry_run: bool):
    """한 파일에서 플래그 키 삭제. (영향받은 (uid, [keys]) 목록, 삭제 개수) 반환."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [건너뜀] {path} 읽기 실패: {e}")
        return [], 0

    if not isinstance(data, dict):
        return [], 0

    affected = []
    removed = 0
    for uid, kv in data.items():
        if not isinstance(kv, dict):
            continue
        hit = [k for k in FLAG_KEYS if k in kv]
        if not hit:
            continue
        affected.append((uid, hit))
        removed += len(hit)
        if not dry_run:
            for k in hit:
                kv.pop(k, None)

    if removed and not dry_run:
        # 원자적 저장(임시파일 → 교체)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    return affected, removed


def main() -> int:
    dry_run = '--dry-run' in sys.argv

    files = _find_state_files()
    if not files:
        print("state/ 아래 game_state.json 을 찾지 못했습니다.")
        return 0

    total = 0
    mode = "[미리보기] " if dry_run else ""
    for path in files:
        affected, removed = _reset_file(path, dry_run)
        rel = path.relative_to(BASE)
        if removed:
            print(f"{mode}{rel} — {len(affected)}명, 플래그 {removed}개")
            for uid, hit in affected:
                print(f"    - {uid}: {', '.join(hit)}")
        else:
            print(f"{mode}{rel} — 해당 플래그 없음")
        total += removed

    print()
    if dry_run:
        print(f"(미리보기 — 총 {total}개가 대상. 실제 리셋은 --dry-run 없이 실행)")
    elif total:
        print(f"완료 — 총 {total}개 플래그 삭제. 봇을 재시작하면 현재값 기준으로 재전송합니다.")
    else:
        print("리셋할 플래그가 없습니다(이미 깨끗함).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
