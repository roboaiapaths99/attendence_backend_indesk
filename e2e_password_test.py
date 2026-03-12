import asyncio
import os
import requests
from dotenv import load_dotenv
from passlib.context import CryptContext
from motor.motor_asyncio import AsyncIOMotorClient

async def run_live_test():
    # Assuming server is running on localhost:8000
    base_url = "http://127.0.0.1:8000"
    
    print("Starting E2E Flow...")
    
    load_dotenv()
    mongo_uri = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "lmsfull")
    db_client = AsyncIOMotorClient(mongo_uri)
    test_db = db_client[db_name]
    test_employees = test_db["employees"]
    
    # Insert test user
    test_email = "test_onboard@example.com"
    test_employee_id = "EMP-TEST-999"
    
    await test_employees.delete_many({"email": test_email})
    
    pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
    hashed_default_pwd = pwd_context.hash(test_employee_id)
    
    await test_employees.insert_one({
        "email": test_email,
        "employee_id": test_employee_id,
        "full_name": "Test Onboarder",
        "hashed_password": hashed_default_pwd,
        "force_password_change": True,
        "organization_id": "test_org",
        "role": "agent"
    })
    
    print("Mock user inserted.")
    
    # Attempt Login with default password
    print("Logging in with default password...")
    resp = requests.post(f"{base_url}/login", json={
        "email": test_email,
        "password": test_employee_id,
        "device_id": "test_device_1"
    })
    
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    assert data["force_password_change"] is True, "User should be flagged for password change"
    
    token = data["access_token"]
    
    # Change password
    print("Attempting to change password...")
    change_resp = requests.post(
        f"{base_url}/api/me/change-password",
        json={"old_password": test_employee_id, "new_password": "newSecurePassword123"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert change_resp.status_code == 200, f"Change password failed: {change_resp.text}"
    
    # Verify it works
    print("Logging in with NEW password...")
    resp2 = requests.post(f"{base_url}/login", json={
        "email": test_email,
        "password": "newSecurePassword123",
        "device_id": "test_device_1"
    })
    assert resp2.status_code == 200, f"Login with new password failed: {resp2.text}"
    data2 = resp2.json()
    assert data2.get("force_password_change", False) is False, "Flag should be cleared"
    
    # Cleanup
    await test_employees.delete_one({"email": test_email})
    print("E2E Backend Test Passed Successfully!")

if __name__ == "__main__":
    asyncio.run(run_live_test())
