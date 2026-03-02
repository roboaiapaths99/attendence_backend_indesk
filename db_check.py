import motor.motor_asyncio
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check():
    url = os.getenv('MONGODB_URL')
    if not url:
        print("MONGODB_URL not found in .env")
        return
    client = motor.motor_asyncio.AsyncIOMotorClient(url)
    db = client.attendance_db
    collections = await db.list_collection_names()
    print(f"Collections: {collections}")
    for c in collections:
        count = await db[c].count_documents({})
        print(f"{c}: {count}")

if __name__ == "__main__":
    asyncio.run(check())
