"""
Quick test to check current reward status
"""
import os
import sys

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

def test_current_system():
    try:
        from config.settings import config
        from utils.sheets_operations import get_sheets_manager
        from core.toot_counter import get_toot_counter
        
        print("=== 현재 시스템 상태 확인 ===")
        
        # 1. 시트 매니저 초기화
        sheets_manager = get_sheets_manager()
        print("✓ 시트 매니저 초기화 성공")
        
        # 2. 툿수 관리 시트에서 사용자 조회
        toot_users = sheets_manager.get_toot_users()
        print(f"✓ 툿수 관리 시트 사용자: {len(toot_users)}명")
        
        # 3. 각 사용자의 재화 계산 상황 확인
        for user in toot_users[:5]:  # 처음 5명만 확인
            user_id = user.get(config.TOOT_SHEET_HEADERS['ID'], '')
            username = user.get(config.TOOT_SHEET_HEADERS['NAME'], 'Unknown')
            recent_toots = int(user.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))
            total_rewards = int(user.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0))
            
            # 재화 계산
            expected_rewards = (recent_toots // config.TOOTS_PER_REWARD) * config.REWARD_AMOUNT
            missing_rewards = max(0, expected_rewards - total_rewards)
            
            print(f"\n사용자: {username} ({user_id})")
            print(f"  현재 툿수: {recent_toots}")
            print(f"  누적 지급 재화: {total_rewards}")
            print(f"  예상 총 재화: {expected_rewards}")
            print(f"  부족분: {missing_rewards}")
            
            if missing_rewards > 0:
                print(f"  → {missing_rewards}재화 지급 필요!")
            else:
                print(f"  → 지급할 재화 없음")
                
        print("\n=== 테스트 완료 ===")
        return True
        
    except Exception as e:
        print(f"오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_current_system()