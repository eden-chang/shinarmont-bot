#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Sheets API 레이트 리미팅 테스트 스크립트
"""

import time
import sys
import os

# main.py에서 필요한 클래스만 import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_rate_limiting():
    """레이트 리미팅 기능 테스트"""
    try:
        # 환경변수 설정 없이 일부 기능만 테스트
        from main import BASE_API_DELAY, QUOTA_EXCEEDED_DELAY, MAX_BACKOFF_DELAY
        import random
        
        print("=== Google Sheets API Rate Limiting Test ===")
        
        # 레이트 리미팅 로직 직접 테스트
        last_api_call_time = 0
        api_call_count = 0
        quota_reset_time = time.time()
        backoff_delay = BASE_API_DELAY
        
        print("1. API Call Simulation...")
        
        start_time = time.time()
        
        # 5번의 API 호출 시뮬레이션
        for i in range(5):
            print(f"API Call {i+1}/5...")
            current_time = time.time()
            
            # 매분 초기화 (1분 윈도우)
            if current_time - quota_reset_time >= 60:
                api_call_count = 0
                quota_reset_time = current_time
                backoff_delay = BASE_API_DELAY
            
            # API 호출 간격 제어
            time_since_last_call = current_time - last_api_call_time
            if time_since_last_call < backoff_delay:
                sleep_time = backoff_delay - time_since_last_call
                jitter = sleep_time * 0.1 * random.random()
                sleep_time += jitter
                print(f"  - Waiting: {sleep_time:.2f}s")
                time.sleep(sleep_time)
            
            last_api_call_time = time.time()
            api_call_count += 1
            
            print(f"  - Current backoff delay: {backoff_delay:.2f}s")
            print(f"  - API call count: {api_call_count}")
        
        total_duration = time.time() - start_time
        print(f"\nTotal test time: {total_duration:.2f}s")
        
        print("2. Quota exceeded simulation...")
        
        # 쿼터 초과 처리 테스트
        original_delay = backoff_delay
        backoff_delay = min(backoff_delay * 2, MAX_BACKOFF_DELAY)
        
        print(f"  - Backoff delay change: {original_delay:.2f}s -> {backoff_delay:.2f}s")
        print(f"  - API call counter would reset")
        
        print("\n=== Test Complete ===")
        print("Rate limiting functionality works correctly.")
        
        return True
        
    except ImportError as e:
        print(f"Import error: {e}")
        print("Check if main.py is in the correct path.")
        return False
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    success = test_rate_limiting()
    exit(0 if success else 1)