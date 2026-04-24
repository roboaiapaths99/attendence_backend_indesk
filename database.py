import motor.motor_asyncio
import os
import certifi
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME", "logday")

client = motor.motor_asyncio.AsyncIOMotorClient(
    MONGODB_URL,
    serverSelectionTimeoutMS=5000,   # fail fast if Atlas is unreachable
    connectTimeoutMS=5000,
    socketTimeoutMS=20000,
    tlsCAFile=certifi.where(),
)
db = client[DATABASE_NAME]

async def get_database():
    return db

# Collections
employees_collection = db["employees"]
attendance_logs_collection = db["attendance_logs"]
settings_collection = db["settings"]
admins_collection = db["admins"]
organizations_collection = db["organizations"]
visit_plans_collection = db["visit_plans"]
visit_logs_collection = db["visit_logs"]
location_pings_collection = db["location_pings"]
km_reimbursements_collection = db["km_reimbursements"]
expense_claims_collection = db["expense_claims"]
otps_collection = db["otps"]
alerts_collection = db["alerts"]
leave_requests_collection = db["leave_requests"]
visit_plan_templates_collection = db["visit_plan_templates"]
nudge_logs_collection = db["nudge_logs"]
