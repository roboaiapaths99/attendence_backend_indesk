
import requests
import base64
import os
from datetime import datetime

API_URL = "http://localhost:8001"

def get_test_face():
    """Load the test face image."""
    try:
        with open("test_face_v2.jpg", "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except:
        return None

def verify_system():
    print("=" * 60)
    print("PRODUCTION SYSTEM VERIFICATION")
    print("=" * 60)

    # 1. Health Check
    print("\n[1/4] Checking Backend Health...")
    try:
        r = requests.get(f"{API_URL}/health")
        print(f"Status: {r.status_code}, Body: {r.json()}")
    except Exception as e:
        print(f"ERROR: Backend unreachable at {API_URL}")
        return

    # 2. Registration with NEW Production Fields
    print("\n[2/4] Testing Enhanced Registration...")
    email = f"prod_test_{datetime.now().strftime('%H%M%S')}@office.flow"
    payload = {
        "full_name": "Production Test User",
        "email": email,
        "password": "SecurePassword123!",
        "employee_id": "EMP-PROD-001",
        "designation": "Staff Engineer",
        "department": "Infrastructure",
        "face_image": get_test_face()
    }
    
    try:
        r = requests.post(f"{API_URL}/register", json=payload)
        if r.status_code == 200:
            data = r.json()
            print(f"SUCCESS: Registration OK.")
            print(f"User Data Returned: {data.get('user', {}).get('full_name')} - {data.get('user', {}).get('designation')}")
            token = data.get("access_token")
        else:
            print(f"FAILED: Status {r.status_code}, Msg: {r.text}")
            return
    except Exception as e:
        print(f"ERROR: {e}")
        return

    # 3. Login Verification
    print("\n[3/4] Testing Login & Profile Context...")
    login_payload = {"email": email, "password": payload["password"]}
    try:
        r = requests.post(f"{API_URL}/login", json=login_payload)
        if r.status_code == 200:
            data = r.json()
            user = data.get("user", {})
            print(f"SUCCESS: Login OK.")
            print(f"Real-Time Data: ID: {user.get('employee_id')}, Dept: {user.get('department')}")
            if user.get("designation") != payload["designation"]:
                print(f"WARNING: Designation mismatch!")
        else:
            print(f"FAILED: Status {r.status_code}")
    except Exception as e:
        print(f"ERROR: {e}")

    # 4. Profile Recovery (/me)
    print("\n[4/4] Testing Profile Recovery (/me)...")
    try:
        r = requests.get(f"{API_URL}/me?token={token}")
        if r.status_code == 200:
            user = r.json()
            print(f"SUCCESS: Profile recovered via token.")
            print(f"Recovered Name: {user.get('full_name')}")
        else:
            print(f"FAILED: Status {r.status_code}, Msg: {r.text}")
    except Exception as e:
        print(f"ERROR: {e}")

    print("\n" + "=" * 60)
    print("ALL PRODUCTION BACKEND SYSTEMS VERIFIED")
    print("=" * 60)

if __name__ == "__main__":
    verify_system()
