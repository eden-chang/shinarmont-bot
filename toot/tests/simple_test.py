"""
Simple test without unicode characters
"""
import os
import sys

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

def simple_test():
    try:
        # Direct import without logging dependencies
        from pathlib import Path
        from dotenv import load_dotenv
        import gspread
        
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
        
        # Open toot sheet
        toot_sheet_name = os.getenv('TOOT_SHEET_NAME', '툿수 관리 자동봇')
        toot_worksheet_name = os.getenv('TOOT_WORKSHEET_NAME', '관리')
        toot_sheet = client.open(toot_sheet_name)
        worksheet = toot_sheet.worksheet(toot_worksheet_name)
        print("[OK] Toot sheet opened")
        
        # Get user data
        users_data = worksheet.get_all_records()
        print(f"[OK] Found {len(users_data)} users")
        
        # Check each user
        toots_per_reward = 100
        reward_amount = 1
        
        # Check all users to find high toot counts
        high_toot_users = []
        for user in users_data:
            recent_toots = int(user.get('최근 확인 툿수', 0))
            if recent_toots >= 100:  # Find users with 100+ toots
                high_toot_users.append(user)
        
        print(f"[INFO] Users with 100+ toots: {len(high_toot_users)}")
        
        # Look for specific discrepancies
        discrepancy_users = []
        for user in users_data:
            recent_toots = int(user.get('최근 확인 툿수', 0))
            total_rewards = int(user.get('누적 지급 재화', 0))
            expected_rewards = (recent_toots // toots_per_reward) * reward_amount
            
            if expected_rewards > total_rewards:
                discrepancy_users.append((user, expected_rewards - total_rewards))
        
        print(f"[INFO] Users with reward discrepancies: {len(discrepancy_users)}")
        
        users_to_check = [user for user, _ in discrepancy_users[:10]]  # Top 10 discrepancies
        if not users_to_check:
            users_to_check = high_toot_users[:5] if high_toot_users else users_data[:5]
        
        for user in users_to_check:
            username = user.get('이름', 'Unknown')
            recent_toots = int(user.get('최근 확인 툿수', 0))
            total_rewards = int(user.get('누적 지급 재화', 0))
            
            # Calculate expected rewards
            expected_rewards = (recent_toots // toots_per_reward) * reward_amount
            missing_rewards = max(0, expected_rewards - total_rewards)
            
            print(f"\nUser: {username}")
            print(f"  Current toots: {recent_toots}")
            print(f"  Total rewards given: {total_rewards}")
            print(f"  Expected total rewards: {expected_rewards}")
            print(f"  Missing rewards: {missing_rewards}")
            
            if missing_rewards > 0:
                print(f"  -> Need to give {missing_rewards} rewards!")
            else:
                print(f"  -> No rewards needed")
                
        return True
        
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    simple_test()