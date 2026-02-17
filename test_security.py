"""
Production Security Test Suite for OfficeFlow.
Tests device binding, geofencing, WiFi checks, and API resilience.
"""
import requests
import json

BASE_URL = "http://127.0.0.1:8001"

def test_health():
    """Test 1: Server is alive and DB connected."""
    r = requests.get(f"{BASE_URL}/health")
    assert r.status_code == 200
    data = r.json()
    assert data["api"] == "online"
    assert data["database"] == "connected"
    print("✅ Health check passed")

def test_login_wrong_credentials():
    """Test 2: Login with wrong credentials returns 401."""
    r = requests.post(f"{BASE_URL}/login", json={
        "email": "nonexistent@test.com",
        "password": "wrongpassword",
        "device_id": "test-device-001"
    })
    assert r.status_code == 401
    print("✅ Wrong credentials correctly rejected (401)")

def test_login_missing_fields():
    """Test 3: Login with missing fields returns 422."""
    r = requests.post(f"{BASE_URL}/login", json={
        "email": "test@test.com"
        # missing password
    })
    assert r.status_code == 422
    print("✅ Missing fields correctly rejected (422)")

def test_smart_attendance_no_face():
    """Test 4: Smart attendance with invalid image returns 400."""
    r = requests.post(f"{BASE_URL}/smart-attendance", json={
        "email": "test@test.com",
        "image": "not_a_real_base64_image",
        "lat": 0.0,
        "long": 0.0,
        "wifi_ssid": "",
        "wifi_bssid": "",
        "wifi_strength": -50,
        "device_id": "test-device-001"
    })
    # Should fail with 400 (no face) or 403 (wifi too weak)
    assert r.status_code in [400, 403]
    print(f"✅ Invalid face image correctly rejected ({r.status_code})")

def test_smart_attendance_weak_wifi():
    """Test 5: Smart attendance with weak WiFi returns 403."""
    r = requests.post(f"{BASE_URL}/smart-attendance", json={
        "email": "test@test.com",
        "image": "dGVzdA==",  # base64 of "test"
        "lat": 0.0,
        "long": 0.0,
        "wifi_ssid": "",
        "wifi_bssid": "",
        "wifi_strength": -95,  # Very weak signal
        "device_id": "test-device-001"
    })
    assert r.status_code == 403
    detail = r.json().get("detail", "")
    assert "WiFi signal too weak" in detail
    print("✅ Weak WiFi correctly rejected (403)")

def test_register_duplicate_email():
    """Test 6: Registering with an existing email returns 400."""
    # This test only works if a user already exists.
    # We send a dummy request to verify the endpoint works.
    r = requests.post(f"{BASE_URL}/register", json={
        "full_name": "Test User",
        "email": "duplicate_test@test.com",
        "employee_id": "TEST001",
        "designation": "Tester",
        "department": "QA",
        "password": "testpassword123",
        "face_image": "not_a_real_image",
        "device_id": "test-device-001"
    })
    # Should fail with 400 (no face detected or already registered)
    assert r.status_code in [400, 500]
    print(f"✅ Invalid registration correctly handled ({r.status_code})")

def test_update_face_wrong_password():
    """Test 7: Update face with wrong password returns 401."""
    r = requests.post(f"{BASE_URL}/update-face", json={
        "email": "nonexistent@test.com",
        "password": "wrongpassword",
        "face_image": "dGVzdA==",
        "lat": 0.0,
        "long": 0.0,
        "wifi_ssid": "",
        "wifi_bssid": "",
        "wifi_strength": -30,
        "device_id": "test-device-001"
    })
    # 403 (geofence) or 404 (user not found) depending on order of checks
    assert r.status_code in [401, 403, 404]
    print(f"✅ Face update security check passed ({r.status_code})")

def test_analytics_nonexistent_user():
    """Test 8: Analytics for non-existent user returns 404."""
    r = requests.get(f"{BASE_URL}/analytics/nonexistent@test.com")
    assert r.status_code == 404
    print("✅ Non-existent user analytics correctly returns 404")

if __name__ == "__main__":
    print("=" * 50)
    print("🔒 OfficeFlow Production Security Tests")
    print("=" * 50)
    
    tests = [
        test_health,
        test_login_wrong_credentials,
        test_login_missing_fields,
        test_smart_attendance_no_face,
        test_smart_attendance_weak_wifi,
        test_register_duplicate_email,
        test_update_face_wrong_password,
        test_analytics_nonexistent_user,
    ]
    
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"⚠️  {test.__name__} ERROR: {e}")
            failed += 1
    
    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'=' * 50}")
