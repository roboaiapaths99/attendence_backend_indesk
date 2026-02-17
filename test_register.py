"""
Test script that simulates the exact registration flow from the phone.
Creates a test face image, encodes it as base64, and sends it to /register.
"""
import requests
import base64
import numpy as np
import cv2

API_URL = "http://localhost:8001"

def create_test_face_image():
    """Load local test face image."""
    try:
        with open("test_face.jpg", "rb") as f:
            b64_string = base64.b64encode(f.read()).decode('utf-8')
            return b64_string
    except Exception as e:
        print(f"Error loading local image: {e}")
        return None

def test_health():
    """Test health endpoint first."""
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        print(f"[Health] Status: {r.status_code}, Response: {r.json()}")
        return r.status_code == 200
    except Exception as e:
        print(f"[Health] FAILED: {e}")
        return False

def test_register():
    """Test registration endpoint."""
    face_b64 = create_test_face_image()
    print(f"[Register] Test face image base64 length: {len(face_b64)}")
    
    payload = {
        "full_name": "Test User",
        "email": "testuser_auto@gmail.com",
        "employee_id": "999999",
        "password": "testpass123",
        "face_image": face_b64
    }
    
    try:
        r = requests.post(f"{API_URL}/register", json=payload, timeout=60)
        print(f"[Register] Status: {r.status_code}")
        print(f"[Register] Response: {r.text[:500]}")
        return r.status_code == 200
    except Exception as e:
        print(f"[Register] FAILED: {e}")
        return False

def test_login():
    """Test login with registered user."""
    payload = {
        "email": "testuser_auto@gmail.com",
        "password": "testpass123"
    }
    try:
        r = requests.post(f"{API_URL}/login", json=payload, timeout=10)
        print(f"[Login] Status: {r.status_code}")
        print(f"[Login] Response: {r.text[:500]}")
        return r.status_code == 200
    except Exception as e:
        print(f"[Login] FAILED: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("Testing OfficeFlow API")
    print("=" * 50)
    
    # 1. Health
    print("\n--- Health Check ---")
    if not test_health():
        print("Backend not reachable. Exiting.")
        exit(1)
    
    # 2. Register
    print("\n--- Registration Test ---")
    reg_ok = test_register()
    
    # 3. Login (only if registration succeeded)
    if reg_ok:
        print("\n--- Login Test ---")
        test_login()
    else:
        print("\nSkipping login test (registration failed).")
    
    print("\n" + "=" * 50)
    print("Tests complete.")
