
import asyncio
from database import employees_collection

async def peek_hashes():
    print("Peeking at user hashes...")
    cursor = employees_collection.find({}, {"email": 1, "hashed_password": 1})
    users = await cursor.to_list(length=5)
    for u in users:
        p = u.get("hashed_password", "")
        print(f"User: {u['email']}, Hash start: {p[:10]}...")

if __name__ == "__main__":
    asyncio.run(peek_hashes())
