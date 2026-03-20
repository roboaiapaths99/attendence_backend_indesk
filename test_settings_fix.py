import requests
import json
import uuid
from datetime import datetime

BASE_URL = "http://localhost:8001"

def test_settings_endpoint():
    print("--- Starting Settings Endpoint Test ---")
    
    # 1. Register Organization to get a fresh admin
    org_slug = f"test-settings-{uuid.uuid4().hex[:4]}"
    admin_email = f"admin@{org_slug}.com"
    org_payload = {
        "org_name": "Settings Test Org",
        "org_slug": org_slug,
        "admin_email": admin_email,
        "admin_password": "Password123",
        "admin_full_name": "Settings Admin"
    }
    
    print(f"Registering organization: {org_slug}...")
    resp = requests.post(f"{BASE_URL}/admin/register-organization", json=org_payload)
    if resp.status_code != 200:
        print(f"FAILED Org Registration: {resp.text}")
        return
    
    # 2. Admin Login
    print("Logging in...")
    login_payload = {"email": admin_email, "password": "Password123"}
    resp = requests.post(f"{BASE_URL}/admin/login", json=login_payload)
    if resp.status_code != 200:
        print(f"FAILED Admin Login: {resp.text}")
        return
    
    admin_token = resp.json().get("access_token")
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # 3. GET Settings
    print("Fetching settings (Initial)...")
    resp = requests.get(f"{BASE_URL}/admin/settings", headers=headers)
    if resp.status_code == 200:
        print("SUCCESS: Settings fetched successfully!")
        settings = resp.json()
        print(f"Settings received: {json.dumps(settings, indent=2)}")
        
        # Verify no ObjectId in the response (they should be strings)
        if "_id" in settings:
            print(f"Checking _id type: {type(settings['_id'])}")
            if not isinstance(settings["_id"], str):
                print("FAILED: _id is not a string!")
            else:
                print("PASSED: _id is a string.")
    else:
        print(f"FAILED: GET /admin/settings returned {resp.status_code}")
        print(f"Response: {resp.text}")
        return

    # 4. PUT Settings
    print("\nUpdating settings...")
    update_payload = settings.copy()
    if "_id" in update_payload:
        del update_payload["_id"] # Pydantic model doesn't expect _id
    
    update_payload["office_start_time"] = "08:30"
    update_payload["late_threshold_mins"] = 20
    
    resp = requests.put(f"{BASE_URL}/admin/settings", json=update_payload, headers=headers)
    if resp.status_code == 200:
        print("SUCCESS: Settings updated successfully!")
    else:
        print(f"FAILED: PUT /admin/settings returned {resp.status_code}")
        print(f"Response: {resp.text}")
        return

    # 5. GET Settings Again
    print("\nFetching settings (After Update)...")
    resp = requests.get(f"{BASE_URL}/admin/settings", headers=headers)
    if resp.status_code == 200:
        updated_settings = resp.json()
        print(f"Updated settings: {json.dumps(updated_settings, indent=2)}")
        if updated_settings.get("office_start_time") == "08:30":
            print("PASSED: Settings change persisted.")
        else:
            print("FAILED: Settings change not persisted.")
    else:
        print(f"FAILED: GET /admin/settings returned {resp.status_code}")
        return

    print("\n--- Settings Endpoint Test Completed ---")

if __name__ == "__main__":
    test_settings_endpoint()
