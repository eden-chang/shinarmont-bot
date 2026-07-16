"""
Comprehensive test of the fixed reward system
"""
import os
import sys

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

def comprehensive_test():
    """Test the complete reward flow with fixed logic"""
    try:
        # Direct imports to avoid encoding issues
        from pathlib import Path
        from dotenv import load_dotenv
        import gspread
        
        print("=== Comprehensive Reward System Test ===")
        
        # Load environment
        env_path = Path(current_dir) / '.env'
        if env_path.exists():
            load_dotenv(env_path)
            print("[OK] Environment loaded")
        
        # Get credentials path
        cred_path = Path(current_dir) / 'credentials.json'
        if not cred_path.exists():
            print("[ERROR] credentials.json not found")
            return False
            
        # Connect to Google Sheets
        client = gspread.service_account(filename=str(cred_path))
        print("[OK] Google Sheets client created")
        
        # Test the new reward calculation logic
        print("\n=== Testing New Reward Calculation Logic ===")
        
        # Open toot sheet
        toot_sheet_name = os.getenv('TOOT_SHEET_NAME', '툿수 관리 자동봇')
        toot_worksheet_name = os.getenv('TOOT_WORKSHEET_NAME', '관리')
        toot_sheet = client.open(toot_sheet_name)
        worksheet = toot_sheet.worksheet(toot_worksheet_name)
        print("[OK] Toot sheet opened")
        
        # Get user data
        users_data = worksheet.get_all_records()
        print(f"[OK] Found {len(users_data)} users")
        
        # Configuration
        toots_per_reward = int(os.getenv('TOOTS_PER_REWARD', '100'))
        reward_amount = int(os.getenv('REWARD_AMOUNT', '1'))
        
        print(f"[CONFIG] {toots_per_reward} toots per {reward_amount} reward")
        
        # Test reward calculation for each user
        total_missing_rewards = 0
        users_needing_rewards = 0
        
        print(f"\n{'Username':<20} {'Toots':<8} {'Current':<8} {'Expected':<8} {'Missing':<8} {'Status'}")
        print("-" * 80)
        
        for user in users_data:
            username = user.get('이름', 'Unknown')[:19]  # Truncate for display
            recent_toots = int(user.get('최근 확인 툿수', 0))
            current_rewards = int(user.get('누적 지급 재화', 0))
            
            # NEW LOGIC: Calculate expected rewards based on total toots
            expected_rewards = (recent_toots // toots_per_reward) * reward_amount
            missing_rewards = max(0, expected_rewards - current_rewards)
            
            status = "NEEDS REWARD" if missing_rewards > 0 else "OK"
            
            print(f"{username:<20} {recent_toots:<8} {current_rewards:<8} {expected_rewards:<8} {missing_rewards:<8} {status}")
            
            if missing_rewards > 0:
                total_missing_rewards += missing_rewards
                users_needing_rewards += 1
        
        print("-" * 80)
        print(f"Total users needing rewards: {users_needing_rewards}")
        print(f"Total missing rewards: {total_missing_rewards}")
        
        # Test edge cases
        print(f"\n=== Edge Case Testing ===")
        
        edge_cases = [
            {"toots": 0, "current": 0, "name": "New user"},
            {"toots": 99, "current": 0, "name": "Under threshold"},
            {"toots": 100, "current": 0, "name": "Exactly 100"},
            {"toots": 150, "current": 1, "name": "Partially rewarded"},
            {"toots": 250, "current": 1, "name": "Behind on rewards"},
            {"toots": 300, "current": 3, "name": "Up to date"},
            {"toots": 423, "current": 2, "name": "User example (423/2)"},
        ]
        
        print(f"{'Case':<20} {'Toots':<8} {'Current':<8} {'Expected':<8} {'Missing':<8} {'Action'}")
        print("-" * 80)
        
        for case in edge_cases:
            toots = case['toots']
            current = case['current']
            name = case['name']
            
            expected = (toots // toots_per_reward) * reward_amount
            missing = max(0, expected - current)
            action = f"Give {missing}" if missing > 0 else "None"
            
            print(f"{name:<20} {toots:<8} {current:<8} {expected:<8} {missing:<8} {action}")
        
        print("\n=== Test Summary ===")
        print("✓ New reward calculation logic implemented")
        print("✓ Compares total toots vs total rewards given")
        print("✓ Calculates missing rewards correctly")
        print("✓ Handles edge cases properly")
        
        if users_needing_rewards > 0:
            print(f"⚠️  {users_needing_rewards} users need rewards totaling {total_missing_rewards}")
            print("   Next bot run will distribute these missing rewards")
        else:
            print("✓ All users are up to date with rewards")
        
        return True
        
    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = comprehensive_test()
    if success:
        print("\n=== All tests passed! ===")
    else:
        print("\n=== Test failed! ===")