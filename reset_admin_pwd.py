import asyncio
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from argon2 import PasswordHasher

load_dotenv()
ph = PasswordHasher()

async def reset_pass():
    client = AsyncIOMotorClient(os.getenv('MONGODB_URL'))
    db = client[os.getenv('DATABASE_NAME')]
    hashed = ph.hash("test")
    res = await db.admins.update_one({'email': 'roboaiapaths99@gmail.com'}, {'$set': {'hashed_password': hashed}})
    print("Modified count:", res.modified_count)

asyncio.run(reset_pass())
