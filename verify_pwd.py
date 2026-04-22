"""Verify admin password works"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from argon2 import PasswordHasher
from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME", "logday")
ph = PasswordHasher()

async def main():
    client = AsyncIOMotorClient(MONGODB_URL, tlsAllowInvalidCertificates=True)
    db = client[DATABASE_NAME]
    admins = db["admins"]

    admin = await admins.find_one({"email": "roboaiapaths99@gmail.com"})
    try:
        result = ph.verify(admin["hashed_password"], "Admin@123")
        print(f"Password verify result: {result}")
        print("LOGIN WILL WORK NOW - use email: roboaiapaths99@gmail.com password: Admin@123")
    except Exception as e:
        print(f"Verify failed: {e}")

    client.close()

asyncio.run(main())
