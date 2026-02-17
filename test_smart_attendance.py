import requests
import base64
import time

API_URL = "http://localhost:8001"
# Use the coordinates from .env (s7 central mall)
OFFICE_LAT = 28.4145947
OFFICE_LONG = 77.354079
# Target SSID MUST MATCH .env or be ignored due to logic.
# Wait, main.py checks OFFICE_WIFI_SSID env var.
# Let's assume .env has "Airtel_rash_1093".
WIFI_SSID = "Airtel_rash_1093" 

def load_face_image():
    # Force download fresh image to ensure quality
    try:
        url = "https://raw.githubusercontent.com/ageitgey/face_recognition/master/examples/obama.jpg"
        print(f"Downloading test face from {url}...")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open("test_face_v2.jpg", "wb") as f:
                f.write(r.content)
            return base64.b64encode(r.content).decode('utf-8')
        else:
            print(f"Failed to download image. Status: {r.status_code}")
            return None
    except Exception as e:
        print(f"Error downloading image: {e}")
        return None

def register_smart_user(face_b64):
    if not face_b64: return
    
    email = "smart.tester@example.com"
    payload = {
        "full_name": "Smart Tester",
        "email": email,
        "employee_id": "SMART001",
        "password": "pass",
        "face_image": face_b64
    }
    try:
        requests.post(f"{API_URL}/register", json=payload)
    except:
        pass
    return email

def test_smart_flow():
    face_b64 = load_face_image()
    if not face_b64: return

    register_smart_user(face_b64)
    
    print("\n--- 1. Testing Weak WiFi (-90 dBm) ---")
    # Should be REJECTED (403)
    try:
        res = requests.post(f"{API_URL}/smart-attendance", json={
            "email": "dummy@email.com", 
            "image": face_b64,
            "lat": OFFICE_LAT,
            "long": OFFICE_LONG,
            "wifi_ssid": WIFI_SSID,
            "wifi_bssid": "a0:91:ca:9b:76:aa",
            "wifi_strength": -90 
        })
        print(f"Status: {res.status_code}")
        if res.status_code == 403:
            print("✅ PASS: Weak WiFi rejected.")
        else:
            print(f"❌ FAIL: Expected 403, got {res.status_code}. Msg: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- 2. Testing Strong WiFi (-40 dBm) [Expect Check-In] ---")
    try:
        res = requests.post(f"{API_URL}/smart-attendance", json={
            "email": "dummy@email.com",
            "image": face_b64,
            "lat": OFFICE_LAT,
            "long": OFFICE_LONG,
            "wifi_ssid": WIFI_SSID,
            "wifi_bssid": "a0:91:ca:9b:76:aa",
            "wifi_strength": -40
        })
        print(f"Status: {res.status_code}")
        print(f"Result: {res.text}")
        if res.status_code == 200 and "check-in" in res.text.lower():
             print("✅ PASS: Check-in successful.")
        else:
             print("❌ FAIL Check-in.")
    except Exception as e:
        print(f"Error: {e}")

    time.sleep(2) 

    print("\n--- 3. Testing Strong WiFi Again [Expect Check-Out] ---")
    try:
        res = requests.post(f"{API_URL}/smart-attendance", json={
            "email": "dummy@email.com",
            "image": face_b64,
            "lat": OFFICE_LAT,
            "long": OFFICE_LONG,
            "wifi_ssid": WIFI_SSID,
            "wifi_bssid": "a0:91:ca:9b:76:aa",
            "wifi_strength": -40
        })
        print(f"Status: {res.status_code}")
        print(f"Result: {res.text}")
        if res.status_code == 200 and "check-out" in res.text.lower():
             print("✅ PASS: Check-out successful.")
        else:
             print("❌ FAIL Check-out.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_smart_flow()
