import motor.motor_asyncio
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME", "officeflow")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
db = client[DATABASE_NAME]

async def get_database():
    return db

# Collections
employees_collection = db["employees"]
attendance_logs_collection = db["attendance_logs"]
