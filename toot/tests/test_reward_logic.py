"""
재화 지급 로직 테스트 스크립트
"""

def test_reward_calculation():
    """재화 지급 계산 로직 테스트"""
    print("=== 재화 지급 계산 로직 테스트 ===")
    
    # 테스트 케이스들
    test_cases = [
        {"toots": 423, "current_rewards": 2, "expected": "4재화 지급 필요 (부족분: 2재화)"},
        {"toots": 350, "current_rewards": 3, "expected": "3재화 지급 필요 (부족분: 0재화)"},
        {"toots": 199, "current_rewards": 0, "expected": "1재화 지급 필요 (부족분: 1재화)"},
        {"toots": 99, "current_rewards": 0, "expected": "0재화 지급 필요 (부족분: 0재화)"},
        {"toots": 500, "current_rewards": 1, "expected": "5재화 지급 필요 (부족분: 4재화)"},
    ]
    
    toots_per_reward = 100
    reward_amount = 1
    
    for i, case in enumerate(test_cases, 1):
        new_count = case["toots"]
        current_total_rewards = case["current_rewards"]
        
        # 새로운 계산 로직
        expected_total_rewards = (new_count // toots_per_reward) * reward_amount
        missing_rewards = max(0, expected_total_rewards - current_total_rewards)
        reward_eligible = missing_rewards > 0
        
        print(f"\n테스트 {i}:")
        print(f"  현재 툿수: {new_count}")
        print(f"  누적 지급 재화: {current_total_rewards}")
        print(f"  예상 총 재화: {expected_total_rewards}")
        print(f"  부족분: {missing_rewards}")
        print(f"  재화 지급 여부: {reward_eligible}")
        print(f"  예상 결과: {case['expected']}")
        
        if reward_eligible:
            print(f"  → {missing_rewards}재화 지급!")
        else:
            print(f"  → 지급할 재화 없음")

if __name__ == "__main__":
    test_reward_calculation()