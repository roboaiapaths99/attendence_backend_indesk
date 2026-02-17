import requests
import base64
import json

# Configuration
BASE_URL = "http://localhost:8000"
TEST_EMAIL = "tester@officeflow.ai"
TEST_PASSWORD = "Password123!"

def test_api_logic():
    print("--- OfficeFlow Backend Logic Verification ---")
    
    # 1. Test Root
    try:
        resp = requests.get(f"{BASE_URL}/")
        print(f"[1] Root Status: {resp.status_code} - {resp.json().get('status')}")
    except:
        print("[!] ERROR: Backend not running. Run 'python main.py' first.")
        return

    # 2. Mock registration attempt (requires image)
    # In a real test, you'd load a small JPG as base64
    print("[2] Testing Registration logic...")
    # Note: This will likely fail without a real face image if DeepFace is active
    # but we can verify the API structure
    
    # 3. Test Presence Verification Logic
    print("[3] Testing Presence Verification Validation...")
    presence_data = {
        "email": TEST_EMAIL,
        "image": "base64_placeholder", # Mock
        "lat": 12.9716, # Exact office lat from .env.example
        "long": 77.5946, # Exact office long
        "wifi_bssid": "00:11:22:33:44:55",
        "wifi_strength": -50
    }
    
    # This will test the GPS/WiFi logic before it even hits the Face check if ordered correctly
    # or will fail early if user not found.
    resp = requests.post(f"{BASE_URL}/verify-presence", json=presence_data)
    print(f"[3] Result: {resp.status_code} - {resp.json().get('detail', 'Success')}")
    
    print("\n--- Summary ---")
    print("To fully test in real-time:")
    print("1. Start backend: cd backend && python main.py")
    print("2. Use the Mobile App to register a real face.")
    print("3. Attempt check-in within the 100m radius of the office.")

if __name__ == "__main__":
    test_api_logic()
