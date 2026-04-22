"""Reset admin password for roboaiapaths99@gmail.com"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from argon2 import PasswordHasher
from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME", "logday")
ph = PasswordHasher()

TARGET_EMAIL = "roboaiapaths99@gmail.com"
NEW_PASSWORD = "robo#9112"  # <-- Change this to your desired password

async def main():
    client = AsyncIOMotorClient(MONGODB_URL, tlsAllowInvalidCertificates=True)
    db = client[DATABASE_NAME]
    admins = db["admins"]

    new_hash = ph.hash(NEW_PASSWORD)
    
    result = await admins.update_one(
        {"email": TARGET_EMAIL},
        {"$set": {"hashed_password": new_hash}}
    )
    
    if result.modified_count == 1:
        print(f"SUCCESS: Password for '{TARGET_EMAIL}' has been reset to '{NEW_PASSWORD}'")
    else:
        print(f"FAILED: No document was updated. Check if email exists.")
    
    # Verify it works
    admin = await admins.find_one({"email": TARGET_EMAIL})
    try:
        ph.verify(admin["hashed_password"], NEW_PASSWORD)
        print("VERIFICATION: Password verify passed ✓")
    except Exception as e:
        print(f"VERIFICATION FAILED: {e}")

    client.close()

asyncio.run(main())
