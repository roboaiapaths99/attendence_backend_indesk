import asyncio
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

async def list_emps():
    client = AsyncIOMotorClient(os.getenv('MONGODB_URL'))
    db = client[os.getenv('DATABASE_NAME')]
    emps = await db.employees.find({}, {'full_name': 1, 'email': 1, 'organization_id': 1, 'employee_type': 1}).to_list(20)
    print("Employees found:")
    for emp in emps:
        print(f"- {emp.get('full_name')} ({emp.get('email')}) | Org: {emp.get('organization_id')} | Type: {emp.get('employee_type')}")

if __name__ == "__main__":
    asyncio.run(list_emps())
