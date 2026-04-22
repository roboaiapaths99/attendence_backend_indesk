import requests
import uuid

BASE_URL = "http://localhost:8001"

def test_settings_persistence():
    print("--- Testing Admin Settings Persistence ---")
    
    # 1. Register Organization & Admin
    org_slug = f"test-settings-{uuid.uuid4().hex[:6]}"
    admin_email = f"admin@{org_slug}.com"
    org_payload = {
        "org_name": "Settings Test Enterprise",
        "org_slug": org_slug,
        "admin_email": admin_email,
        "admin_password": "Password123",
        "admin_full_name": "Settings Admin"
    }
    
    print(f"Registering org: {org_slug}...")
    resp = requests.post(f"{BASE_URL}/admin/register-organization", json=org_payload)
    if resp.status_code != 200:
        print(f"FAILED Registration: {resp.text}")
        return

    # 2. Admin Login
    print("Logging in...")
    resp = requests.post(f"{BASE_URL}/admin/login", json={"email": admin_email, "password": "Password123"})
    token = resp.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    # 3. GET Settings (Initial/Default)
    print("Fetching default settings...")
    resp = requests.get(f"{BASE_URL}/admin/settings", headers=headers)
    if resp.status_code == 200:
        print(f"Default Settings: {resp.json().get('office_start_time')} (Expected: 09:00)")
    else:
        print(f"FAILED GET Settings: {resp.status_code} {resp.text}")
        return

    # 4. PUT Settings
    print("Updating settings...")
    new_settings = resp.json()
    new_settings.pop("_id", None) # Remove _id for PUT
    new_settings["office_start_time"] = "08:30"
    resp = requests.put(f"{BASE_URL}/admin/settings", json=new_settings, headers=headers)
    print(f"Update Response: {resp.status_code} - {resp.json()}")

    # 5. GET Settings (Verify Update)
    print("Verifying updated settings...")
    resp = requests.get(f"{BASE_URL}/admin/settings", headers=headers)
    if resp.status_code == 200:
        val = resp.json().get('office_start_time')
        print(f"Updated Settings: {val} (Expected: 08:30)")
        if val == "08:30":
            print("SUCCESS: Admin Settings Persistence Verified!")
        else:
            print("FAILED: Value mismatch")
    else:
        print(f"FAILED GET Settings after update: {resp.status_code} {resp.text}")

if __name__ == "__main__":
    test_settings_persistence()
