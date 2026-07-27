# -*- coding: utf-8 -*-
"""이성/건강 경고 문구 '발송 플래그' 리셋 (VM에서 1회 실행).

배경:
  2026-07-27 시작 재전송 스윕 최초 도입 시, DM 전송기 재초기화가 대기 큐를
  버리는 버그로 문구 DM이 실제로는 안 나갔는데 game_state에는 '발송함'으로
  기록됐다. 코드는 고쳤지만(멱등 initialize_dm_sender), 이미 기록된 플래그
  때문에 재시작해도 그 캐릭터들은 skip된다. 이 스크립트로 플래그를 0으로
  되돌리면, 다음 봇 시작 때 '현재값이 임계 이하'인 문구가 다시 발송된다.

무엇을 하나:
  game_state의 모든 캐릭터에 대해 아래 플래그를 0으로 리셋(존재할 때만).
    이성문구1발송 / 이성문구2발송 / 건강문구1발송 / 건강문구2발송
  값을 0으로 두면 check_and_set(…, 1)이 다시 True를 돌려줘 재발송된다.
  (이성/건강 '수치'나 다른 game_state 값은 건드리지 않는다.)

사용:
  python tools/reset_stat_msg_flags.py            # 실제 리셋
  python tools/reset_stat_msg_flags.py --dry-run  # 미리보기(변경 없음)

주의:
  봇을 '멈춘 상태'에서 실행하는 것을 권장한다(실행 중 동시 쓰기 회피).
  리셋 후 봇을 재시작하면 스윕이 재전송한다.
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

from utils import game_state  # noqa: E402

# stat_gate에서 플래그 키를 그대로 가져와 이름 드리프트를 막는다.
try:
    from utils.stat_gate import STAT_MSG_TIERS
    FLAG_KEYS = [tier[4] for tier in STAT_MSG_TIERS]
except Exception:
    # stat_gate 임포트 실패 시 하드코딩 폴백
    FLAG_KEYS = ['이성문구1발송', '이성문구2발송', '건강문구1발송', '건강문구2발송']


def main() -> int:
    dry_run = '--dry-run' in sys.argv

    gs = game_state.get_game_state()
    snapshot = gs.snapshot()  # {uid: {key: val, ...}}

    reset_count = 0
    affected = []
    for uid, kv in snapshot.items():
        hit = [k for k in FLAG_KEYS if k in kv and str(kv.get(k)) not in ('', '0')]
        if not hit:
            continue
        affected.append((uid, hit))
        if not dry_run:
            for k in hit:
                gs.set(uid, k, 0)
        reset_count += len(hit)

    mode = "[미리보기] " if dry_run else ""
    print(f"{mode}대상 캐릭터: {len(affected)}명, 리셋 플래그: {reset_count}개")
    for uid, hit in affected:
        print(f"  - {uid}: {', '.join(hit)}")

    if dry_run:
        print("\n(미리보기 모드 — 아무것도 바꾸지 않았습니다. 실제 리셋은 --dry-run 없이 실행)")
    elif reset_count:
        print("\n완료. 봇을 재시작하면 시작 스윕이 현재값 기준으로 재전송합니다.")
    else:
        print("\n리셋할 플래그가 없습니다(이미 깨끗함).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
