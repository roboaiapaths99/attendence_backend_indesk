import requests
import json
import uuid
import time
from datetime import datetime, timezone

BASE_URL = "http://localhost:8001"

def test_e2e_flow():
    print("--- Starting FieldForce Pro E2E Test ---")
    
    # 1. Health Check
    try:
        resp = requests.get(f"{BASE_URL}/health")
        print(f"Health Check: {resp.status_code} - {resp.json()}")
    except Exception as e:
        print(f"FAILED: Backend not reachable: {e}")
        return

    # 2. Register Organization
    org_slug = f"test-org-{uuid.uuid4().hex[:6]}"
    admin_email = f"admin@{org_slug}.com"
    org_payload = {
        "org_name": "Test Enterprise",
        "org_slug": org_slug,
        "admin_email": admin_email,
        "admin_password": "Password123",
        "admin_full_name": "Test Admin"
    }
    
    resp = requests.post(f"{BASE_URL}/admin/register-organization", json=org_payload)
    if resp.status_code == 200:
        print(f"Org Registered: {org_slug}")
        org_id = resp.json().get("organization_id")
    else:
        print(f"FAILED Org Registration: {resp.text}")
        return

    # 3. Admin Login
    login_payload = {"email": admin_email, "password": "Password123"}
    resp = requests.post(f"{BASE_URL}/admin/login", json=login_payload)
    if resp.status_code == 200:
        admin_token = resp.json().get("access_token")
        print("Admin Logged In Successfully")
    else:
        print(f"FAILED Admin Login: {resp.text}")
        return

    # 4. Register Field Employee
    emp_email = f"field-{uuid.uuid4().hex[:4]}@test.com"
    # Basic face embedding simulation (list of floats)
    emp_payload = {
        "full_name": "Field Agent One",
        "email": emp_email,
        "employee_id": f"EMP-{uuid.uuid4().hex[:4]}",
        "designation": "Sales Executive",
        "department": "Sales",
        "organization_id": org_id,
        "password": "Password123",
        "face_image": "base64_simulated_image", # Backend face_utils needs to handle this
        "employee_type": "field"
    }
    
    # We'll use the public /register for convenience in testing
    resp = requests.post(f"{BASE_URL}/register", json=emp_payload)
    if resp.status_code == 200:
        print(f"Employee Registered: {emp_email}")
        emp_token = resp.json().get("access_token")
    else:
        print(f"FAILED Employee Registration: {resp.text}")
        return

    # 5. Submit Visit Plan
    plan_payload = {
        "employee_id": emp_email, # backend maps by email in some places, employee_id in others. Let's be careful.
        "organization_id": org_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "stops": [
            {
                "sequence_order": 1,
                "place_name": "Client A",
                "place_lat": 28.6139,
                "place_lng": 77.2090
            }
        ]
    }
    headers = {"Authorization": f"Bearer {emp_token}"}
    resp = requests.post(f"{BASE_URL}/api/field/plan", json=plan_payload, headers=headers)
    if resp.status_code == 200:
        print("Visit Plan Submitted")
        plan_id = resp.json().get("plan_id")
    else:
        print(f"FAILED Plan Submission: {resp.text}")
        return

    # 6. Admin Approves Plan
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    resp = requests.post(f"{BASE_URL}/admin/field/visit-plans/{plan_id}/approve", headers=admin_headers)
    if resp.status_code == 200:
        print("Plan Approved by Admin")
    else:
        # Check if the endpoint path is correct
        print(f"FAILED Plan Approval: {resp.status_code} - {resp.text}")
        # Retrying with different action param if needed
        pass

    # 7. Morning Attendance (Clock-in)
    att_payload = {
        "email": emp_email,
        "image": "base64_simulated_image",
        "lat": 28.6139,
        "long": 77.2090,
        "mock_detected": False
    }
    resp = requests.post(f"{BASE_URL}/smart-attendance", json=att_payload, headers=headers)
    if resp.status_code == 200:
        print("Attendance Marked (Clock-in)")
    else:
        print(f"FAILED Attendance: {resp.text}")

    # 9. Verify Admin Stats
    resp = requests.get(f"{BASE_URL}/admin/stats", headers=admin_headers)
    if resp.status_code == 200:
        print(f"Admin Stats verified: {resp.json()}")
    else:
        print(f"FAILED Admin Stats: {resp.text}")

    # 10. Verify Admin Logs
    resp = requests.get(f"{BASE_URL}/admin/logs", headers=admin_headers)
    if resp.status_code == 200:
        print(f"Admin Logs verified (count: {len(resp.json())})")
    else:
        print(f"FAILED Admin Logs: {resp.text}")

    print("--- E2E Test Completed ---")

if __name__ == "__main__":
    test_e2e_flow()
