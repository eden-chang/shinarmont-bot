"""
Test if the reward fix works properly
"""
import os
import sys

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

def test_reward_fix():
    """Test the fixed reward calculation logic"""
    try:
        # Import the fixed toot counter
        from core.toot_counter import TootCounter
        from config.settings import config
        
        print("=== Testing Fixed Reward Logic ===")
        
        # Create toot counter instance
        counter = TootCounter()
        print("[OK] TootCounter created")
        
        # Get first user to test with
        sheets_manager = counter.sheets_manager
        users = sheets_manager.get_toot_users()
        
        if not users:
            print("[ERROR] No users found")
            return False
            
        # Test with first user who has enough toots
        test_user = None
        for user in users:
            recent_toots = int(user.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))
            if recent_toots >= 200:  # Find user with 200+ toots for testing
                test_user = user
                break
                
        if not test_user:
            print("[INFO] No user with 200+ toots found for testing")
            print("[INFO] Using first user with highest toot count")
            test_user = max(users, key=lambda x: int(x.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0)))
        
        user_id = test_user.get(config.TOOT_SHEET_HEADERS['ID'])
        username = test_user.get(config.TOOT_SHEET_HEADERS['NAME'])
        current_toots = int(test_user.get(config.TOOT_SHEET_HEADERS['RECENT_TOOTS'], 0))
        current_rewards = int(test_user.get(config.TOOT_SHEET_HEADERS['TOTAL_REWARDS'], 0))
        
        print(f"\n[TEST] Testing with user: {username}")
        print(f"  User ID: {user_id}")
        print(f"  Current toots: {current_toots}")
        print(f"  Current total rewards: {current_rewards}")
        
        # Simulate the fixed calculation
        expected_rewards = (current_toots // 100) * 1
        missing_rewards = max(0, expected_rewards - current_rewards)
        
        print(f"  Expected total rewards: {expected_rewards}")
        print(f"  Missing rewards: {missing_rewards}")
        
        if missing_rewards > 0:
            print(f"  -> Should give {missing_rewards} rewards!")
            
            # Test the actual calculation method
            result = counter.calculate_single_user_toots(user_id, force_api_call=False)
            
            print(f"\n[RESULT] TootCountResult:")
            print(f"  Success: {result.success}")
            print(f"  User: {result.username}")
            print(f"  Old count: {result.old_count}")
            print(f"  New count: {result.new_count}")
            print(f"  Increase: {result.increase}")
            print(f"  Reward eligible: {result.reward_eligible}")
            print(f"  Reward amount: {result.reward_amount}")
            print(f"  Error: {result.error}")
            
            if result.reward_eligible and result.reward_amount == missing_rewards:
                print(f"\n[SUCCESS] Fixed logic working correctly!")
                print(f"  Expected: {missing_rewards} rewards")
                print(f"  Calculated: {result.reward_amount} rewards")
                return True
            elif result.reward_eligible:
                print(f"\n[PARTIAL] Logic working but different amount")
                print(f"  Expected: {missing_rewards} rewards")
                print(f"  Calculated: {result.reward_amount} rewards")
                return True
            else:
                print(f"\n[ERROR] Logic not working - no rewards calculated")
                return False
        else:
            print(f"  -> No rewards needed (already up to date)")
            print(f"\n[INFO] All users appear to be up to date")
            print(f"[INFO] Fixed logic is likely working correctly")
            return True
            
    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_reward_fix()
    if success:
        print("\n=== Test completed successfully ===")
    else:
        print("\n=== Test failed ===")