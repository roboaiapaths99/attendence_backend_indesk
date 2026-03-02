import requests
import json
import uuid
from datetime import datetime, timezone

BASE_URL = "http://localhost:8001"

def test_v2_features():
    print("=" * 60)
    print("   FIELD FORCE v2.0 FEATURE TEST")
    print("=" * 60)

    # ── 1. Health Check ────────────────────────────────────────
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"[HEALTH] {r.status_code} — {r.json()}")
    except Exception as e:
        print(f"[FAIL] Backend not reachable: {e}")
        return

    # ── 2. Setup: Register org & field employee ─────────────────
    slug = f"v2test-{uuid.uuid4().hex[:5]}"
    admin_email = f"admin@{slug}.com"
    r = requests.post(f"{BASE_URL}/admin/register-organization", json={
        "org_name": "v2 Test Corp", "org_slug": slug,
        "admin_email": admin_email, "admin_password": "Test1234!",
        "admin_full_name": "v2 Admin"
    })
    if r.status_code != 200:
        print(f"[FAIL] Org setup: {r.text}")
        return
    org_id = r.json()["organization_id"]
    print(f"[SETUP] Org created: {slug} (id={org_id[:8]}...)")

    # Admin login
    r = requests.post(f"{BASE_URL}/admin/login", json={"email": admin_email, "password": "Test1234!"})
    admin_token = r.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    emp_email = f"agent-{uuid.uuid4().hex[:4]}@{slug}.com"
    r = requests.post(f"{BASE_URL}/register", json={
        "full_name": "Test Field Agent", "email": emp_email,
        "employee_id": f"EMP-{uuid.uuid4().hex[:4]}",
        "designation": "Sales Rep", "department": "Sales",
        "organization_id": org_id, "password": "Test1234!",
        "face_image": "data:image/png;base64,iVBORw0KGgo=",
        "employee_type": "field"
    })
    if r.status_code != 200:
        print(f"[WARN] Employee registration: {r.status_code} {r.text[:80]}")
    else:
        emp_token = r.json()["access_token"]
        emp_headers = {"Authorization": f"Bearer {emp_token}"}
        print(f"[SETUP] Employee: {emp_email}")

        # ── 3. Test Plan Template ──────────────────────────────
        print("\n[TEST 1] POST /api/field/plan/template")
        r = requests.post(f"{BASE_URL}/api/field/plan/template", json={
            "template_name": "Weekly Milk Run",
            "stops": [
                {"sequence_order": 1, "place_name": "Client A", "place_lat": 28.61, "place_lng": 77.21},
                {"sequence_order": 2, "place_name": "Client B", "place_lat": 28.62, "place_lng": 77.22},
            ],
            "recurrence_days": [0, 1, 2, 3, 4]
        }, headers=emp_headers)
        if r.status_code == 200:
            template_id = r.json()["template_id"]
            print(f"  ✅ Template created: id={template_id[:8]}...")
        else:
            print(f"  ❌ {r.status_code}: {r.text[:100]}")
            template_id = None

        # ── 4. Get Templates ───────────────────────────────────
        print(f"\n[TEST 2] GET /api/field/plan/templates/{emp_email}")
        r = requests.get(f"{BASE_URL}/api/field/plan/templates/{emp_email}", headers=emp_headers)
        if r.status_code == 200:
            templates = r.json()
            print(f"  ✅ Templates listed: {len(templates)} found")
        else:
            print(f"  ❌ {r.status_code}: {r.text[:100]}")

        # ── 5. Leaderboard ─────────────────────────────────────
        print("\n[TEST 3] GET /api/field/leaderboard")
        r = requests.get(f"{BASE_URL}/api/field/leaderboard", headers=emp_headers)
        if r.status_code == 200:
            lb = r.json()
            print(f"  ✅ Leaderboard OK — week: {lb.get('week_start')} → {lb.get('week_end')}")
            print(f"     Top entries: {len(lb.get('leaderboard', []))}")
        else:
            print(f"  ❌ {r.status_code}: {r.text[:100]}")

        # ── 6. Manager Nudge (no subordinates yet → expect 403) ─
        print("\n[TEST 4] POST /api/manager/nudge (expects 403 — no subordinates)")
        r = requests.post(f"{BASE_URL}/api/manager/nudge", json={
            "employee_emails": [emp_email],
            "message": "Keep going team!",
            "nudge_type": "general"
        }, headers=emp_headers)
        if r.status_code == 403:
            print(f"  ✅ Correctly rejected (no subordinates): {r.json()['detail']}")
        elif r.status_code == 200:
            print(f"  ✅ Nudge sent: {r.json()}")
        else:
            print(f"  ❌ {r.status_code}: {r.text[:100]}")

        # ── 7. Expense with visit_plan_stop_id ─────────────────
        print("\n[TEST 5] POST /api/field/expenses (with visit_plan_stop_id)")
        r = requests.post(f"{BASE_URL}/api/field/expenses", json={
            "expense_type": "toll",
            "amount": 150.0,
            "description": "Highway toll at stop 1",
            "receipt_url": "",
            "visit_plan_stop_id": "stop_seq_1"
        }, headers=emp_headers)
        if r.status_code == 200:
            print(f"  ✅ Expense with stop tag: {r.json()}")
        else:
            print(f"  ❌ {r.status_code}: {r.text[:100]}")

    print("\n" + "=" * 60)
    print("   TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    test_v2_features()
