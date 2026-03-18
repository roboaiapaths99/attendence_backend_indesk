from fastapi import FastAPI, HTTPException, File, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os
import math
import pandas as pd
import io
from bson import ObjectId
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from dotenv import load_dotenv
from typing import Optional, List, Dict
import logging
import base64
import uuid
from fastapi.staticfiles import StaticFiles

# Configure Logging
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "backend.log"))
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize Scheduler
scheduler = AsyncIOScheduler()

from database import (
    employees_collection, attendance_logs_collection, settings_collection, admins_collection, 
    organizations_collection, visit_plans_collection, visit_logs_collection, 
    location_pings_collection, km_reimbursements_collection, expense_claims_collection, otps_collection,
    alerts_collection, leave_requests_collection,
    visit_plan_templates_collection, nudge_logs_collection
)
from models import (
    RegisterRequest, LoginRequest, VerifyPresenceRequest, Token, LoginResponse, EmployeeProfile, UpdateFaceRequest,
    AdminLoginRequest, EmployeeUpdate, SystemSettings, Admin, Organization, OrganizationRegisterRequest, SubAdminCreate,
    EmployeeType, TerritoryType, AttendanceType, CheckInMethod, PlanStatus, VisitPlan, Visit, LocationPing, ExpenseClaim,
    LeaveType, LeaveStatus, DiscussionMessage, LeaveRequest, SyncBatchRequest, ChangePasswordRequest
)
from auth import (
    get_password_hash, verify_password, create_access_token, 
    get_current_admin, get_current_employee, admin_oauth2_scheme, employee_oauth2_scheme
)
from face_utils import get_face_embedding, verify_face, compare_faces
from sheets_sync import sync_to_google_sheets, sync_visit_to_google_sheets
from fastapi import BackgroundTasks

APP_ENV = os.getenv("APP_ENV", "development")

app = FastAPI(
    title="Log Day AI Attendance API",
    version="1.0.0",
    docs_url="/docs" if APP_ENV != "production" else None,
    redoc_url="/redoc" if APP_ENV != "production" else None,
)

# Configure CORS
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
if not _raw_origins:
    # Default to development origins
    _allowed_origins = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://192.168.1.6:5173",
        "http://192.168.1.6:5174",
        "http://localhost:3000",
        "https://attendence-inofice-admin-desk-bza5cuwtz.vercel.app",
        "https://attendence-inofice-admin-desk.vercel.app",
    ]
elif _raw_origins == "*":
    _allowed_origins = ["*"]
else:
    _allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins + [
        "http://localhost",
        "http://10.0.2.2", # Android emulator
    ],
    allow_origin_regex=r"https?://.*", # Very permissive for debugging, will narrow later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# --- GLOBAL EXCEPTION HANDLERS ---
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Standardize HTTP exception responses for frontend mapping."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": f"HTTP_{exc.status_code}",
            "detail": exc.detail,
            "type": "standard_error"
        },
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Catch-all for internal server errors to avoid leaking tracebacks in production."""
    logger.exception(f"Unhandled error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": "INTERNAL_SERVER_ERROR",
            "detail": "An unexpected error occurred on the server." if APP_ENV == "production" else str(exc),
            "type": "system_error"
        },
    )

# --- STARTUP EVENT ---
@app.on_event("startup")
async def startup_event():
    """Ensure database indexes are present for performance optimization."""
    # Each index op is isolated so a MongoDB SSL/network issue won't crash startup
    index_ops = [
        ("employees_email", employees_collection, [("email", 1)], {"unique": True, "background": True}),
        ("attendance_logs", attendance_logs_collection, [("user_id", 1), ("timestamp", -1)], {"background": True}),
        ("visit_plans", visit_plans_collection, [("employee_id", 1), ("status", 1)], {"background": True}),
        ("visit_logs", visit_logs_collection, [("employee_id", 1), ("timestamp", -1)], {"background": True}),
        ("location_pings", location_pings_collection, [("employee_id", 1), ("recorded_at", -1)], {"background": True}),
        ("km_reimbursements", km_reimbursements_collection, [("employee_id", 1), ("date", -1)], {"background": True}),
    ]
    for name, col, keys, kwargs in index_ops:
        try:
            await col.create_index(keys, **kwargs)
        except Exception as e:
            logger.warning(f"Index '{name}' skipped (non-fatal): {type(e).__name__}: {str(e)[:60]}")

    # Scheduler startup
    try:
        if not scheduler.running:
            scheduler.start()
        logger.info("Scheduler started successfully.")
    except Exception as e:
        logger.warning(f"Scheduler startup failed (non-fatal): {e}")

    logger.info(f"Application startup complete. ENV={APP_ENV}")

# --- HEALTH CHECK ENDPOINT ---
# (Moved to line 284 for consolidation)

async def send_security_alert_notification(alert_type: str, employee_email: str, detail: str):
    """
    Mock function for sending SMS/WhatsApp alerts.
    In production, this would integrate with Twilio, AWS SNS, etc.
    """
    logger.info(f"CRITICAL SECURITY NOTIFICATION sent to Admin: [{alert_type}] User: {employee_email} - {detail}")
    # Integration logic here (e.g., Twilio API)

async def trigger_alert(alert_type: str, employee_id: str, organization_id: str, detail: str, severity: str = "medium", metadata: dict = None):
    """Log a security or operational alert to the database."""
    try:
        alert = {
            "type": alert_type, # Identity, Territory, Productivity, Compliance
            "employee_id": employee_id,
            "organization_id": organization_id,
            "detail": detail,
            "severity": severity, # low, medium, high, critical
            "timestamp": datetime.now(timezone.utc),
            "status": "pending", # pending, resolved, dismissed
            "metadata": metadata or {}
        }
        await alerts_collection.insert_one(alert)
        logger.warning(f"ALERT TRIGGERED [{alert_type}]: {detail} for {employee_id}")
        
        # If high severity, send out external notification
        if severity in ["high", "critical"]:
            await send_security_alert_notification(alert_type, employee_id, detail)
            
    except Exception as e:
        logger.error(f"Failed to trigger alert: {e}")

# Ensure uploads directory exists
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# Serve static files
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

async def check_missed_visits():
    """
    Scheduled job to check for missed visits.
    A visit is considered missed if it was approved for today but not checked in.
    """
    logger.info("Running scheduled job: check_missed_visits")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Find all approved plans for today
    approved_plans_cursor = visit_plans_collection.find({
        "date": today_str,
        "status": PlanStatus.APPROVED
    })
    
    async for plan in approved_plans_cursor:
        employee_id = plan["employee_id"]
        organization_id = plan["organization_id"]
        
        for stop in plan.get("stops", []):
            stop_id = stop.get("sequence_order") # Assuming sequence_order is the unique stop identifier
            
            # Check if a visit log exists for this employee, date, and stop_id
            existing_log = await visit_logs_collection.find_one({
                "employee_id": employee_id,
                "date": today_str,
                "visit_plan_stop_id": stop_id
            })
            
            if not existing_log:
                # This stop was planned but not checked in
                detail = f"Missed visit: Employee {employee_id} did not check in to planned stop '{stop.get('place_name')}' (Stop ID: {stop_id})."
                await trigger_alert(
                    "Productivity",
                    employee_id,
                    organization_id,
                    detail,
                    "medium",
                    {"plan_id": str(plan["_id"]), "stop_id": stop_id, "place_name": stop.get("place_name")}
                )
                logger.warning(detail)
    logger.info("Finished scheduled job: check_missed_visits")


@app.on_event("startup")
async def startup_db_client():
    """Create indexes on startup (non-fatal if DB is temporarily unreachable)."""
    try:
        # Core Indexes
        await employees_collection.create_index("email", unique=True)
        await employees_collection.create_index("employee_id")
        await attendance_logs_collection.create_index([("user_id", 1), ("timestamp", -1)])
        
        # Enterprise Indexes
        await employees_collection.create_index("organization_id")
        await attendance_logs_collection.create_index("organization_id")
        
        # Field Force GIS Indexes
        await location_pings_collection.create_index([("location", "2dsphere")])
        await location_pings_collection.create_index([("employee_id", 1), ("recorded_at", -1)])
        await visit_logs_collection.create_index([("check_in_location", "2dsphere")])
        await otps_collection.create_index("expires_at", expireAfterSeconds=0)
        
        logger.info("MongoDB Enterprise & GIS Indexes created successfully.")
    except Exception as e:
        logger.warning(f"Startup index creation skipped (non-fatal): {type(e).__name__}: {str(e)[:80]}")

    # Start Scheduler
    try:
        if not scheduler.running:
            scheduler.add_job(check_missed_visits, 'interval', hours=1)
            scheduler.start()
        logger.info("APScheduler started: check_missed_visits scheduled hourly.")
    except Exception as e:
        logger.warning(f"Scheduler already running or failed to start: {e}")
    
    print("Startup complete.")


@app.get("/")
async def root():
    return {"message": "LogDay AI Attendance API is active", "status": "online"}


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint to verify API and DB are connected."""
    try:
        from database import client
        await client.admin.command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    return {
        "api": "LogDay AI Attendance API",
        "status": "healthy",
        "env": APP_ENV,
        "database": db_status,
        "version": "1.0.0"
    }


@app.post("/register", response_model=LoginResponse)
async def register(req: RegisterRequest):
    """Register a new employee with face image and enterprise metadata."""
    try:
        logger.info(f"Received registration request for: {req.email}")

        # Check if employee already exists
        existing = await employees_collection.find_one({"email": req.email})
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        # Generate face embedding
        if not req.face_image:
            raise HTTPException(status_code=400, detail="face_image is required for registration")
        
        embedding = get_face_embedding(req.face_image)
        if embedding is None:
            raise HTTPException(status_code=400, detail="No face detected in image. Please try again with a clear photo.")

        # Create employee record
        hashed_password = get_password_hash(req.password)
        employee_dict = {
            "full_name": req.full_name,
            "email": req.email,
            "employee_id": req.employee_id,
            "designation": req.designation,
            "department": req.department,
            "organization_id": req.organization_id,
            "employee_type": req.employee_type,
            "hashed_password": hashed_password,
            "face_embedding": embedding,
            "profile_image": req.face_image,
            "device_id": req.device_id,
            "created_at": datetime.now(timezone.utc),
            "status": "Active"
        }

        # For Field employees, initialize default territory if not provided
        if req.employee_type == EmployeeType.FIELD:
            employee_dict.update({
                "territory_type": TerritoryType.RADIUS,
                "territory_radius_meters": 500, # Default 500m
                "gps_otp_fallback_enabled": True
            })

        await employees_collection.insert_one(employee_dict)
        logger.info(f"User {req.email} saved to database successfully as {req.employee_type}.")

        # Generate token
        access_token = create_access_token(data={"sub": req.email})
        
        response_data = {
            "access_token": access_token, 
            "token_type": "bearer",
            "user": {
                "full_name": employee_dict["full_name"],
                "email": employee_dict["email"],
                "employee_id": employee_dict["employee_id"],
                "designation": employee_dict["designation"],
                "department": employee_dict["department"],
                "organization_id": employee_dict["organization_id"],
                "employee_type": employee_dict["employee_type"],
                "created_at": employee_dict["created_at"],
                "profile_image": employee_dict["profile_image"]
            }
        }
        try:
            return LoginResponse(**response_data)
        except Exception as e:
            logger.error(f"Register Response Validation Failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Registration Validation Error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration failed for {req.email}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Registration failed due to an internal server error.")



@app.get("/analytics/me")
async def get_analytics(current_user: dict = Depends(get_current_employee)):
    """Retrieve weekly/daily work hour stats for the authenticated user (JWT Protected)."""
    email = current_user["email"]
    
    user = await employees_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    from datetime import timedelta
    now_utc = datetime.now(timezone.utc)
    seven_days_ago = (now_utc - timedelta(days=7)).isoformat()
    today_str = now_utc.strftime("%Y-%m-%d")

    # Get all logs for this user in the last 7 days
    all_logs_cursor = attendance_logs_collection.find({
        "user_id": str(user["_id"]),
        "timestamp": {"$gte": seven_days_ago}
    }).sort("timestamp", -1)
    all_logs = await all_logs_cursor.to_list(length=500)
    
    # We also need the VERY LATEST log (regardless of the 7-day window) for the current status
    latest_log = await attendance_logs_collection.find_one(
        {"user_id": str(user["_id"])},
        sort=[("timestamp", -1)]
    )

    daily_hours = {}
    total_week_hours = 0.0
    today_hours = 0.0
    on_time_count = 0
    current_status = "check-out"

    # Set current status based on the absolute latest log
    if latest_log:
        current_status = latest_log.get("type", "check-out")

    # Process logs for hours and stats
    for log in all_logs:
        log_time_raw = log.get("timestamp")
        if not log_time_raw:
            continue

        try:
            if isinstance(log_time_raw, str):
                log_time = datetime.fromisoformat(log_time_raw.replace("Z", "+00:00"))
            elif isinstance(log_time_raw, datetime):
                log_time = log_time_raw if log_time_raw.tzinfo else log_time_raw.replace(tzinfo=timezone.utc)
            else:
                continue
        except Exception:
            continue

        date_str = log_time.strftime("%Y-%m-%d")
        log_type = log.get("type", "")

        # Accumulate hours from check-out logs
        if log_type == "check-out":
            duration = float(log.get("duration_hours", 0) or 0)
            daily_hours[date_str] = daily_hours.get(date_str, 0.0) + duration
            total_week_hours += duration
            if date_str == today_str:
                today_hours += duration

        # Count on-time check-ins (before 09:15)
        if log_type == "check-in":
            if log_time.hour < 9 or (log_time.hour == 9 and log_time.minute <= 15):
                on_time_count += 1

    return {
        "today_hours": round(today_hours, 2),
        "week_total": round(total_week_hours, 2),
        "daily_breakdown": daily_hours,
        "current_status": current_status,
        "on_time_count": on_time_count,
        "total_logs_week": len(all_logs),
        "office_wifi_ssid": os.getenv("OFFICE_WIFI_SSID", "").strip()
    }


@app.post("/update-face")
async def update_face(req: UpdateFaceRequest):
    """Securely update user face descriptors after location & password verification."""
    # 1. Geofencing Validation (Strict 4m as requested)
    office_lat = float(os.getenv("OFFICE_LAT", 0))
    office_long = float(os.getenv("OFFICE_LONG", 0))
    radius = float(os.getenv("GEOFENCE_RADIUS_METERS", 4))
    
    dlat = math.radians(req.lat - office_lat)
    dlon = math.radians(req.long - office_long)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(office_lat)) * math.cos(math.radians(req.lat)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_meters = 6371000 * c
    
    if dist_meters > radius:
        raise HTTPException(
            status_code=403,
            detail=f"Biometric update restricted to Office Zone. You are {dist_meters:.0f}m away."
        )

    # 2. WiFi Validation
    target_ssid = os.getenv("OFFICE_WIFI_SSID", "")
    target_bssid = os.getenv("OFFICE_WIFI_BSSID", "")
    wifi_pct = max(0, min(100, 2 * (req.wifi_strength + 100)))
    REQUIRED_WIFI_PCT = 80
    
    if wifi_pct < REQUIRED_WIFI_PCT:
        raise HTTPException(status_code=403, detail=f"WiFi signal too weak ({wifi_pct:.0f}%). Biometric update requires stable connection.")

    if target_ssid and req.wifi_ssid and req.wifi_ssid != target_ssid:
         raise HTTPException(status_code=403, detail=f"Biometric update requires Office WiFi: {target_ssid}")
         
    if target_bssid and req.wifi_bssid and req.wifi_bssid.lower() != target_bssid.lower():
        raise HTTPException(status_code=403, detail="BSSID mismatch. Security restriction for biometric updates.")

    # 3. Identity Verification
    user = await employees_collection.find_one({"email": req.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect password. Unauthorized re-enrollment.")

    # 4. Device Binding Check
    if user.get("device_id") and req.device_id and user["device_id"] != req.device_id:
        raise HTTPException(status_code=403, detail="Security Alert: Hardware ID mismatch. Biometrics must be updated from your registered phone.")
        
    try:
        # Generate face embedding using utility
        embedding = get_face_embedding(req.face_image)
        if embedding is None:
            raise HTTPException(status_code=400, detail="No face detected in the image.")
            
        # Update user record
        await employees_collection.update_one(
            {"email": req.email},
            {"$set": {
                "face_embedding": embedding,
                "profile_image": req.face_image
            }}
        )
        
        return {"message": "Face data updated successfully. No attendance impact."}
        
    except Exception as e:
        logger.error(f"Face update failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Face update failed due to internal server error.")



@app.get("/organization/discover/{slug}")
async def discover_organization(slug: str):
    """Public endpoint for mobile app to discover organization and its branding."""
    # 1. Try exact slug match (case-insensitive)
    org = await organizations_collection.find_one({"slug": slug.lower()})
    
    # 2. Try name match if slug fails (case-insensitive)
    if not org:
        org = await organizations_collection.find_one(
            {"name": {"$regex": f"^{slug}$", "$options": "i"}}
        )

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    return {
        "organization_id": str(org["_id"]),
        "slug": org.get("slug"),
        "name": org.get("name") or org.get("org_name"),
        "logo_url": org.get("logo_url"),
        "primary_color": org.get("primary_color", "#0f172a")
    }


from fastapi import Request

@app.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """Login with email and password. When organization_id is provided, employee must belong to that org (multi-tenant security)."""
    # Log the raw request info
    body = await request.body()
    logger.info(f"Incoming login body: {body.decode('utf-8', errors='ignore')}")
    
    clean_email = req.email.strip().lower()
    logger.info(f"--- Login attempt for '{clean_email}' ---")
    
    user = await employees_collection.find_one({"email": clean_email})
    if not user:
        logger.warning(f"Login failed: User '{clean_email}' not found.")
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    logger.info(f"User found in DB. Stored Org ID: {user.get('organization_id')}")
    
    is_valid = verify_password(req.password, user.get("hashed_password", ""))
    if not is_valid:
        logger.warning(f"Login failed: Password mismatch for '{clean_email}'.")
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    logger.info("Password verification successful.")

    # Org-scoped security: user from one org cannot log in to another org
    if req.organization_id:
        emp_org = user.get("organization_id")
        logger.info(f"Checking Org Match: App sent '{req.organization_id}', DB has '{emp_org}'")
        if emp_org is None:
            logger.warning(f"Login failed: User {clean_email} has no org linked in DB.")
            raise HTTPException(
                status_code=403,
                detail="This account is not linked to an organization. Please contact your administrator."
            )
        if emp_org != req.organization_id:
            logger.warning(f"Login failed: Org mismatch. App: {req.organization_id}, DB: {emp_org}")
            raise HTTPException(
                status_code=403,
                detail="Access denied. You do not belong to this organization."
            )
    
    logger.info("Organization match successful.")

    # Device Binding Check
    if user.get("device_id") and req.device_id and user["device_id"] != req.device_id:
        logger.warning(f"Login failed: Device binding mismatch. App: {req.device_id}, DB: {user['device_id']}")
        raise HTTPException(status_code=403, detail="Security Alert: Account locked to a different device. Please use your registered phone.")
    
    # Auto-bind on first login if not set
    if not user.get("device_id") and req.device_id:
        logger.info(f"Binding user {clean_email} to device {req.device_id}")
        await employees_collection.update_one({"email": clean_email}, {"$set": {"device_id": req.device_id}})
    
    logger.info("Login process complete. Generating token.")
    access_token = create_access_token(data={"sub": req.email})
    
    # Check if this user is a manager (has subordinates)
    subordinates_count = await employees_collection.count_documents({"manager_id": clean_email})
    is_manager = subordinates_count > 0
    
    # Check if user needs face enrollment (no face_embedding)
    needs_enrollment = user.get("face_embedding") is None
    
    # Return full profile with defaults for legacy accounts
    response_data = {
        "access_token": access_token, 
        "token_type": "bearer",
        "user": {
            "full_name": user.get("full_name", "User"),
            "email": user.get("email", req.email),
            "employee_id": user.get("employee_id", "EMP-000"),
            "designation": user.get("designation", "Employee"),
            "department": user.get("department", "General"),
            "organization_id": user.get("organization_id") or "unknown",
            "employee_type": user.get("employee_type") or "desk",
            "created_at": user.get("created_at", datetime.now(timezone.utc)),
            "profile_image": user.get("profile_image"),
            "is_manager": is_manager
        },
        "needs_face_enrollment": needs_enrollment,
        "force_password_change": user.get("force_password_change", False)
    }
    
    try:
        return LoginResponse(**response_data)
    except Exception as e:
        logger.error(f"Login Response Validation Failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Login Validation Error: {str(e)}")


@app.get("/me", response_model=EmployeeProfile)
async def get_me(employee=Depends(get_current_employee)):
    """Retrieve currently authenticated employee profile."""
    # Check if this user is a manager
    subordinates_count = await employees_collection.count_documents({"manager_id": employee["email"]})
    
    return {
        "full_name": employee.get("full_name", "Unknown"),
        "email": employee.get("email"),
        "employee_id": employee.get("employee_id", "0000"),
        "designation": employee.get("designation", "Employee"),
        "department": employee.get("department", "General"),
        "organization_id": employee.get("organization_id", "unknown"),
        "employee_type": employee.get("employee_type", "desk"),
        "created_at": employee.get("created_at", datetime.now(timezone.utc)),
        "profile_image": employee.get("profile_image"),
        "is_manager": subordinates_count > 0
    }

@app.post("/api/me/change-password")
async def change_password(req: ChangePasswordRequest, employee=Depends(get_current_employee)):
    """Change employee password and clear force_password_change flag."""
    user = await employees_collection.find_one({"email": employee["email"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    is_valid = verify_password(req.old_password, user.get("hashed_password", ""))
    if not is_valid:
        raise HTTPException(status_code=400, detail="Incorrect old password")
        
    new_hashed_password = get_password_hash(req.new_password)
    await employees_collection.update_one(
        {"email": employee["email"]},
        {"$set": {
            "hashed_password": new_hashed_password,
            "force_password_change": False
        }}
    )
    return {"status": "success", "message": "Password updated successfully"}


@app.post("/verify-presence")
async def verify_presence(req: VerifyPresenceRequest):
    """Verify user presence using face + GPS + WiFi telemetry."""
    # 1. Find User
    user = await employees_collection.find_one({"email": req.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")

    # 2. Face Verification
    is_match, distance = verify_face(req.image, user["face_embedding"])
    if not is_match:
        raise HTTPException(
            status_code=401,
            detail=f"Face verification failed (distance: {distance:.4f}). Please try again."
        )

    # 3. Geofencing Validation
    office_lat = float(os.getenv("OFFICE_LAT", 0))
    office_long = float(os.getenv("OFFICE_LONG", 0))
    radius = float(os.getenv("GEOFENCE_RADIUS_METERS", 100))

    # Haversine distance (simplified for small distances)
    import math
    dlat = math.radians(req.lat - office_lat)
    dlon = math.radians(req.long - office_long)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(office_lat)) * math.cos(math.radians(req.lat)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_meters = 6371000 * c  # Earth radius in meters

    if dist_meters > radius:
        raise HTTPException(
            status_code=403,
            detail=f"You are {dist_meters:.0f}m away from office. Must be within {radius:.0f}m."
        )

    # 4. WiFi Validation
    target_bssid = os.getenv("OFFICE_WIFI_BSSID", "")
    target_ssid = os.getenv("OFFICE_WIFI_SSID", "")
    min_strength = float(os.getenv("MIN_WIFI_SIGNAL_STRENGTH", -80))

    if target_bssid and req.wifi_bssid and req.wifi_bssid.lower() != target_bssid.lower():
        raise HTTPException(status_code=403, detail="Must be connected to Office WiFi (Bad BSSID)")

    if target_ssid and req.wifi_ssid and req.wifi_ssid != target_ssid:
         raise HTTPException(status_code=403, detail=f"Must be connected to Office WiFi: {target_ssid}")

    if req.wifi_strength < min_strength:
        raise HTTPException(status_code=403, detail="WiFi signal too weak - are you inside the office?")

    # 5. Determine check-in or check-out
    last_log = await attendance_logs_collection.find_one(
        {"user_id": str(user["_id"])},
        sort=[("timestamp", -1)]
    )
    attendance_type = "check-out" if (last_log and last_log.get("type") == "check-in") else "check-in"

    # 6. Log Attendance
    log = {
        "user_id": str(user["_id"]),
        "email": req.email,
        "timestamp": datetime.utcnow(),
        "type": attendance_type,
        "location": {"lat": req.lat, "long": req.long},
        "distance_meters": dist_meters,
        "wifi_info": {"bssid": req.wifi_bssid, "strength": req.wifi_strength},
        "face_confidence": float(distance),
    }
    await attendance_logs_collection.insert_one(log)

    return {
        "status": "success",
        "type": attendance_type,
        "message": f"{attendance_type.replace('-', ' ').title()} recorded at {log['timestamp'].strftime('%I:%M %p')}",
        "time": str(log["timestamp"]),
        "distance_from_office": f"{dist_meters:.1f}m",
    }


@app.post("/smart-attendance")
async def smart_attendance(req: VerifyPresenceRequest, background_tasks: BackgroundTasks):
    """
    Unified endpoint for Enterprise Smart Attendance:
    - DESK: Strict WiFi (80%) + Office Geofence (4m).
    - FIELD: Bypass WiFi. Validate Territory (500m default) OR OTP.
    - Both: Face Liveness/Match + Mock Detection.
    """
    try:
        # 0. Global Telemetry Defaults
        office_lat = float(os.getenv("OFFICE_LAT", 0))
        office_long = float(os.getenv("OFFICE_LONG", 0))
        wifi_pct = 0
        
        # 1. Identity & Role Fetch
        user = await employees_collection.find_one({"email": req.email})
        if not user:
             # Try 1:N face search if email is unknown/auto
            new_embedding = get_face_embedding(req.image)
            if new_embedding is None:
                raise HTTPException(status_code=400, detail="No face detected in image.")
                
            employees = await employees_collection.find({}, {"_id": 1, "face_embedding": 1, "email": 1, "employee_type": 1, "organization_id": 1}).to_list(length=5000)
            for emp in employees:
                if emp.get("face_embedding") and compare_faces(new_embedding, emp["face_embedding"]):
                    user = emp
                    break
            
            if not user:
                raise HTTPException(status_code=404, detail="Identity not recognized. Please sign in or register.")

        # At this point, 'user' is identified
        role = user.get("employee_type", EmployeeType.DESK)
        attendance_type = req.intended_type or "check-in"
        logger.info(f"Processing {attendance_type_str(attendance_type)} for {user['email']} (Role: {role})")

        # Session Validation: Prevent duplicate check-ins or orphan check-outs
        # Removed the 'today_start' restriction to support state-based shifts (e.g. shifts crossing midnight)
        last_log = await attendance_logs_collection.find_one(
            {"user_id": str(user["_id"])},
            sort=[("timestamp", -1)]
        )

        if attendance_type == "check-in":
            if last_log and last_log.get("type") == "check-in":
                raise HTTPException(status_code=400, detail="You are already checked in for today.")
        elif attendance_type == "check-out":
            if not last_log or last_log.get("type") == "check-out":
                raise HTTPException(status_code=400, detail="Cannot check out without an active check-in.")

        # 2. Universal Security: Mock Location & Face Match
        if req.mock_detected:
            background_tasks.add_task(
                trigger_alert, 
                "Territory", 
                user["email"], 
                user["organization_id"], 
                "Mock Location detected during attendance.", 
                "high",
                {"lat": req.lat, "long": req.long}
            )
            raise HTTPException(status_code=403, detail="Security violation: Mock location detected. Attendance rejected.")

        # Device Binding Check
        if user.get("device_id") and req.device_id and user["device_id"] != req.device_id:
            background_tasks.add_task(
                trigger_alert, 
                "Identity", 
                user["email"], 
                user["organization_id"], 
                f"Device mismatch. Registered: {user['device_id']}, Current: {req.device_id}", 
                "medium"
            )
            # We log it but maybe don't block yet or block based on config. For now, let's block to be safe.
            raise HTTPException(status_code=403, detail="Security Violation: This account is bound to another device. Please contact admin.")

        if not user.get("face_embedding"):
            raise HTTPException(status_code=400, detail="Face biometric not enrolled for this user.")
        
        is_match, distance = verify_face(req.image, user["face_embedding"])
        if not is_match:
            background_tasks.add_task(
                trigger_alert, 
                "Identity", 
                user["email"], 
                user["organization_id"], 
                f"Face verification failed with confidence distance {distance:.3f}", 
                "medium"
            )
            raise HTTPException(status_code=401, detail="Face verification failed. Please ensure your face is clearly visible.")

        # 3. Branch Verification Logic
        check_in_method = CheckInMethod.WIFI_GEOFENCE

        if role == EmployeeType.DESK:
            # DESK: Strict WiFi Check
            wifi_pct = max(0, min(100, 2 * (req.wifi_strength + 100)))
            if wifi_pct < 80:
                 raise HTTPException(status_code=403, detail=f"WiFi signal too weak ({wifi_pct:.0f}%). Office attendance requires >= 80% signal.")
            
            # DESK: Strict Office Geofence
            radius = float(os.getenv("GEOFENCE_RADIUS_METERS", 4))
            dist = calculate_haversine(req.lat, req.long, office_lat, office_long)
            
            if dist > radius:
                 raise HTTPException(status_code=403, detail=f"Location error. You are {dist:.1f}m outside the designated office zone.")
            check_in_method = CheckInMethod.WIFI_GEOFENCE

        else:
            # FIELD: Territory OR OTP Validation
            if req.otp_used:
                # --- OTP FALLBACK VALIDATION ---
                if not req.otp_code:
                    raise HTTPException(status_code=400, detail="OTP code required for GPS fallback.")
                
                # Check if OTP matches and is not expired (5 min expiry)
                stored_otp = user.get("gps_otp")
                otp_expiry = user.get("gps_otp_expiry")
                
                if not stored_otp or str(stored_otp) != str(req.otp_code):
                    raise HTTPException(status_code=403, detail="Invalid OTP code.")
                
                if otp_expiry and datetime.now(timezone.utc) > otp_expiry.replace(tzinfo=timezone.utc) if otp_expiry.tzinfo is None else otp_expiry:
                     raise HTTPException(status_code=403, detail="OTP code has expired. Please request a new one.")
                
                # Clear OTP after use
                await employees_collection.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"gps_otp": None, "gps_otp_expiry": None}}
                )
                check_in_method = CheckInMethod.OTP_FALLBACK
            else:
                # Validate Territory
                territory_type = user.get("territory_type", TerritoryType.RADIUS)
                if territory_type == TerritoryType.RADIUS:
                    t_lat = user.get("territory_center_lat", office_lat) # Fallback to office if not set
                    t_lng = user.get("territory_center_lng", office_long)
                    t_radius = user.get("territory_radius_meters", 500)
                    dist = calculate_haversine(req.lat, req.long, t_lat, t_lng)
                    if dist > t_radius:
                        background_tasks.add_task(
                            trigger_alert, 
                            "Territory", 
                            user["email"], 
                            user["organization_id"], 
                            f"Territory Breach. Agent is {dist:.0f}m away from beat zone center.", 
                            "medium",
                            {"lat": req.lat, "long": req.long, "allowed_radius": t_radius}
                        )
                        raise HTTPException(status_code=403, detail=f"Territory Breach. You are {dist:.0f}m away from your assigned beat zone.")
                elif territory_type == TerritoryType.POLYGON:
                    polygon = user.get("territory_polygon", [])
                    if not polygon or len(polygon) < 3:
                        raise HTTPException(status_code=400, detail="Territory polygon not configured. Contact your admin.")
                    if not is_point_in_polygon(req.lat, req.long, polygon):
                        background_tasks.add_task(
                            trigger_alert, 
                            "Territory", 
                            user["email"], 
                            user["organization_id"], 
                            "Territory Breach. Agent is outside assigned polygon zone.", 
                            "medium",
                            {"lat": req.lat, "long": req.long}
                        )
                        raise HTTPException(status_code=403, detail="Territory Breach. You are outside your assigned beat zone polygon.")
                check_in_method = CheckInMethod.GPS_TERRITORY

        # 4. Log Attendance (Consolidated)
        attendance_type = req.intended_type or "check-in" # Default or provided
        
        log = {
            "user_id": str(user["_id"]),
            "email": user["email"],
            "organization_id": user.get("organization_id"),
            "timestamp": datetime.now(timezone.utc),
            "type": attendance_type,
            "attendance_type": AttendanceType.OFFICE if role == EmployeeType.DESK else AttendanceType.REMOTE_FIELD,
            "location": {"lat": req.lat, "long": req.long},
            "check_in_method": check_in_method,
            "wifi_confidence": wifi_pct if role == EmployeeType.DESK else 0,
            "confidence_score": float(distance),
            "status": "Present",
            "selfie_verified": True,
            "device_id": req.device_id, # Track device for security
            "mock_location_detected": req.mock_detected
        }
        
        await attendance_logs_collection.insert_one(log)
        
        # Trigger Background Sync to Google Sheets
        background_tasks.add_task(sync_to_google_sheets, log)
        
        return {
            "status": "success",
            "type": attendance_type,
            "message": f"Enterprise {attendance_type.replace('-', ' ')} recorded via {check_in_method.value}.",
            "time": log["timestamp"].isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Execution Error in smart_attendance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal processing error in attendance module.")


def calculate_haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat1 - lat2)
    dlon = math.radians(lon1 - lon2)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371000 * c


def is_point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
    """Ray-casting algorithm to check if a point is inside a polygon.
    polygon: list of dicts with 'lat' and 'lng' keys.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]["lat"], polygon[i]["lng"]
        xj, yj = polygon[j]["lat"], polygon[j]["lng"]
        if ((yi > lng) != (yj > lng)) and (lat < (xj - xi) * (lng - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def attendance_type_str(val):
    return val or "attendance"




@app.get("/logs/{email}")
async def get_logs(email: str, current_user: dict = Depends(get_current_employee)):
    """Get attendance logs for a user (JWT Protected)."""
    if current_user["email"] != email:
        raise HTTPException(status_code=403, detail="Forbidden: You can only access your own logs.")

    user = await employees_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    cursor = attendance_logs_collection.find(
        {"user_id": str(user["_id"])}
    ).sort("timestamp", -1)
    logs = await cursor.to_list(length=100)

    # Convert ObjectIDs to strings for JSON serialization
    for log in logs:
        log["_id"] = str(log["_id"])
        if "timestamp" in log and isinstance(log["timestamp"], datetime):
            # Ensure it's ISO format with Z for UTC
            ts = log["timestamp"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            log["timestamp"] = ts.isoformat().replace("+00:00", "Z")
        elif "timestamp" in log:
            log["timestamp"] = str(log["timestamp"])

    return {"logs": logs, "count": len(logs)}


@app.post("/admin/import-employees")
async def admin_import_employees(file: bytes = File(...), current_admin: Admin = Depends(get_current_admin)):
    """Bulk import employees from CSV or Excel with upsert and auto-assignment."""
    try:
        org_id = current_admin.organization_id
        if not org_id:
            raise HTTPException(status_code=403, detail="Organization ID missing in admin context")

        # Detect format: try CSV first, then Excel
        try:
            df = pd.read_csv(io.BytesIO(file))
        except Exception:
            try:
                df = pd.read_excel(io.BytesIO(file))
            except Exception:
                raise HTTPException(status_code=400, detail="Unsupported file format. Please upload a .csv or .xlsx file.")
        
        # Normalize column names (strip whitespace, lowercase)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        
        required_cols = ["full_name", "email"]
        for col in required_cols:
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"Missing required column: {col}. Required: full_name, email")
        
        created_count = 0
        updated_count = 0
        error_rows = []
        
        for idx, row in df.iterrows():
            try:
                email = str(row["email"]).strip().lower()
                if not email or email == "nan":
                    error_rows.append({"row": idx + 2, "error": "Missing email"})
                    continue
                    
                existing = await employees_collection.find_one({"email": email})
                
                # Build update fields (only non-empty columns)
                update_fields = {}
                field_map = {
                    "full_name": "full_name",
                    "employee_id": "employee_id",
                    "designation": "designation",
                    "department": "department",
                    "employee_type": "employee_type",
                    "beat_zone_name": "beat_zone_name",
                }
                for csv_col, db_field in field_map.items():
                    if csv_col in df.columns:
                        val = row.get(csv_col)
                        if pd.notna(val) and str(val).strip():
                            update_fields[db_field] = str(val).strip()
                
                # Handle manager_email column for auto-assignment
                if "manager_email" in df.columns:
                    mgr_email = row.get("manager_email")
                    if pd.notna(mgr_email) and str(mgr_email).strip():
                        update_fields["manager_id"] = str(mgr_email).strip().lower()
                
                raw_password = str(row.get("password")).strip() if "password" in df.columns and pd.notna(row.get("password")) and str(row.get("password")).strip() else None
                employee_id = update_fields.get("employee_id", email.split("@")[0])

                if existing:
                    # UPSERT: Update existing employee's metadata
                    if raw_password:
                        update_fields["hashed_password"] = get_password_hash(raw_password)
                        update_fields["force_password_change"] = True

                    if update_fields:
                        await employees_collection.update_one(
                            {"email": email},
                            {"$set": update_fields}
                        )
                        updated_count += 1
                else:
                    # CREATE: New employee
                    final_password = raw_password if raw_password else employee_id
                    
                    employee_dict = {
                        "full_name": update_fields.pop("full_name", email.split("@")[0]),
                        "email": email,
                        "employee_id": employee_id,
                        "designation": update_fields.pop("designation", "Employee"),
                        "department": update_fields.pop("department", "General"),
                        "employee_type": update_fields.pop("employee_type", "desk"),
                        "hashed_password": get_password_hash(final_password),
                        "force_password_change": True,
                        "face_embedding": None,
                        "profile_image": None,
                        "created_at": datetime.now(timezone.utc),
                        "organization_id": org_id,
                        "status": "Active",
                    }
                    if "employee_id" in update_fields: del update_fields["employee_id"]
                    employee_dict.update(update_fields)
                    
                    # For Field employees, initialize territory
                    if employee_dict.get("employee_type") in ["field", "FIELD"]:
                        employee_dict.update({
                            "territory_type": "radius",
                            "territory_radius_meters": 500,
                            "gps_otp_fallback_enabled": True
                        })
                    
                    await employees_collection.insert_one(employee_dict)
                    created_count += 1
                    
            except Exception as e:
                error_rows.append({"row": idx + 2, "error": str(e)[:100]})
        
        return {
            "message": f"Import complete. Created: {created_count}, Updated: {updated_count}, Errors: {len(error_rows)}",
            "created": created_count,
            "updated": updated_count,
            "errors": error_rows[:20]  # Limit error output
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Import failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@app.get("/admin/employees/import-template")
async def get_import_template(current_admin: Admin = Depends(get_current_admin)):
    """Download a CSV template for employee import."""
    csv_content = "full_name,email,employee_id,password,designation,department,employee_type,manager_email,beat_zone_name\n"
    csv_content += "John Doe,john@example.com,EMP001,,Sales Executive,Sales,field,manager@example.com,North Zone\n"
    csv_content += "Jane Smith,jane@example.com,EMP002,Secret@321,Developer,Engineering,desk,,\n"
    
    return StreamingResponse(
        io.BytesIO(csv_content.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=employee_import_template.csv"}
    )


@app.get("/admin/export-logs-pdf")
async def admin_export_logs_pdf(current_admin: Admin = Depends(get_current_admin)):
    """Generate a PDF report of all attendance logs for the admin's organization."""
    try:
        org_id = current_admin.organization_id
        if not org_id:
             raise HTTPException(status_code=403, detail="Organization context required")

        # Get all employee IDs for this org
        org_employees = await employees_collection.find({"organization_id": org_id}, {"_id": 1}).to_list(None)
        org_emp_ids = [str(emp["_id"]) for emp in org_employees]

        cursor = attendance_logs_collection.find({"user_id": {"$in": org_emp_ids}}).sort("timestamp", -1)
        logs = await cursor.to_list(length=5000)
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
        elements = []
        
        styles = getSampleStyleSheet()
        elements.append(Paragraph("LogDay Attendance Audit Report", styles['Title']))
        elements.append(Paragraph(f"Generated on: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}", styles['Normal']))
        elements.append(Spacer(1, 20))
        
        data = [["Timestamp", "Employee Name", "Type", "Location", "Verification"]]
        for log in logs:
            ts = log.get("timestamp")
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts_str = str(ts)
                
            data.append([
                ts_str,
                log.get("full_name") or log.get("email") or "Unknown",
                log.get("type", "check-in").upper(),
                log.get("address", "Main Office"),
                log.get("status", "SUCCESS").upper()
            ])
            
        table = Table(data, hAlign='LEFT', colWidths=[150, 150, 100, 200, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#7c3aed")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey)
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=attendance_report_{datetime.now().strftime('%Y%m%d')}.pdf"}
    )
    except Exception as e:
        logger.error(f"PDF export error: {e}")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")


@app.get("/admin/export-logs-excel")
async def admin_export_logs_excel(current_admin: Admin = Depends(get_current_admin)):
    """Export attendance logs to Excel for the admin's organization."""
    try:
        org_id = current_admin.organization_id
        if not org_id:
             raise HTTPException(status_code=403, detail="Organization context required")

        org_employees = await employees_collection.find({"organization_id": org_id}, {"_id": 1}).to_list(None)
        org_emp_ids = [str(emp["_id"]) for emp in org_employees]

        logs = await attendance_logs_collection.find({"user_id": {"$in": org_emp_ids}}).sort("timestamp", -1).to_list(5000)
        
        # Flatten logs for Excel
        data = []
        for log in logs:
            data.append({
                "Employee": log.get("full_name") or log.get("email"),
                "Email": log.get("email"),
                "Time": log.get("timestamp").strftime("%Y-%m-%d %H:%M:%S") if log.get("timestamp") else "N/A",
                "Type": log.get("type").title(),
                "Late": "Yes" if log.get("is_late") else "No",
                "Late Mins": log.get("late_mins", 0),
                "Address": log.get("address"),
                "WiFi": log.get("wifi_ssid"),
                "Distance (m)": round(log.get("distance_meters", 0), 1),
                "Duration (h)": log.get("duration_hours", 0)
            })
        
        df = pd.DataFrame(data)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Attendance Logs')
        
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=attendance_logs_{datetime.now().strftime('%Y%m%d')}.xlsx"}
        )
    except Exception as e:
        logger.error(f"Excel export error: {e}")
        raise HTTPException(status_code=500, detail=f"Excel export failed: {str(e)}")


# --- ADMIN ENDPOINTS ---

@app.post("/admin/login")
async def admin_login(req: AdminLoginRequest):
    """Database-driven admin authentication."""
    # 1. Check database for admin
    admin = await admins_collection.find_one({"email": req.email})
    
    # 2. Verify password
    if admin and verify_password(req.password, admin.get("hashed_password")):
        token_data = {"sub": req.email, "role": admin.get("role", "admin")}
        # Add Organization Context if present
        if admin.get("organization_id"):
            token_data["org_id"] = admin.get("organization_id")
            
        token = create_access_token(data=token_data)
        
        # Fetch Org details for branding if applicable
        org_details = {}
        if admin.get("organization_id"):
            try:
                org = await organizations_collection.find_one({"_id": ObjectId(admin.get("organization_id"))})
                if org:
                    org_details = {
                        "id": str(org["_id"]),
                        "name": org.get("name"),
                        "slug": org.get("slug"),
                        "logo_url": org.get("logo_url"),
                        "primary_color": org.get("primary_color")
                    }
            except Exception:
                pass

        return {
            "access_token": token, 
            "token_type": "bearer", 
            "user": {
                "name": admin.get("full_name", "Administrator"), 
                "email": admin.get("email"),
                "organization_id": admin.get("organization_id"),
                "role": admin.get("role")
            },
            "organization": org_details
        }
    
    # 3. Fallback to hardcoded env (temporary safety net / migration)
    admin_email = os.getenv("ADMIN_EMAIL", "admin@officeflow.ai")
    admin_pass = os.getenv("ADMIN_PASSWORD", "admin123")
    
    if req.email == admin_email and req.password == admin_pass:
        token = create_access_token(data={"sub": req.email, "role": "superadmin"})
        return {"access_token": token, "token_type": "bearer", "user": {"name": "Super Admin", "email": admin_email, "role": "superadmin"}}

    raise HTTPException(status_code=401, detail="Invalid admin credentials")


@app.post("/admin/register-organization")
async def register_organization(req: OrganizationRegisterRequest):
    """
    Public Endpoint: Registers a new Organization and its Account Owner (Super Admin).
    """
    try:
        # 1. Check if Slug or Email exists
        if await organizations_collection.find_one({"slug": req.org_slug}):
             raise HTTPException(status_code=400, detail="Organization ID (slug) already taken.")
        
        if await admins_collection.find_one({"email": req.admin_email}):
            raise HTTPException(status_code=400, detail="Admin email already registered.")

        # 2. Create Organization
        new_org = {
            "name": req.org_name,
            "slug": req.org_slug,
            "logo_url": req.logo_url,
            "primary_color": req.primary_color,
            "created_at": datetime.utcnow()
        }
        org_result = await organizations_collection.insert_one(new_org)
        org_id = str(org_result.inserted_id)

        # 3. Create Org Admin (Owner)
        hashed_password = get_password_hash(req.admin_password)
        new_admin = {
            "email": req.admin_email,
            "hashed_password": hashed_password,
            "full_name": req.admin_full_name,
            "role": "owner",
            "organization_id": org_id,  # Link to the new Org
            "created_at": datetime.utcnow()
        }
        await admins_collection.insert_one(new_admin)

        # 4. Initialize Default Settings for this Org
        default_settings = {
            "organization_id": org_id,
            "office_start_time": "09:00",
            "late_threshold_mins": 15,
            "half_day_hours": 4,
            "full_day_hours": 8,
            "updated_at": datetime.utcnow()
        }
        await settings_collection.insert_one(default_settings)

        return {
            "message": f"Organization '{req.org_name}' registered successfully!",
            "organization_id": org_id,
            "org_slug": req.org_slug,
            "admin_email": req.admin_email
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Organization registration failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Organization registration failed.")



@app.get("/admin/sub-admins")
async def list_sub_admins(current_admin: Admin = Depends(get_current_admin)):
    """List all admins for the organization (Owner/Superadmin only)."""
    if current_admin.role not in ["owner", "superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Only organization owners/admins can manage the admin team.")
    
    query = {"organization_id": current_admin.organization_id}
    cursor = admins_collection.find(query, {"hashed_password": 0})
    admins = await cursor.to_list(length=100)
    for a in admins:
        a["_id"] = str(a["_id"])
    return admins


@app.post("/admin/sub-admins")
async def create_sub_admin(req: SubAdminCreate, current_admin: Admin = Depends(get_current_admin)):
    """Create a new sub-admin for the organization (Owner/Superadmin only)."""
    if current_admin.role not in ["owner", "superadmin", "admin"]:
        logger.warning(f"Access Denied: Admin {current_admin.email} with role {current_admin.role} tried to manage sub-admins.")
        raise HTTPException(status_code=403, detail="Only organization owners/admins can add new admins.")
    
    # Check if admin already exists
    existing = await admins_collection.find_one({"email": req.email})
    if existing:
        raise HTTPException(status_code=400, detail="Admin email already registered.")
    
    hashed_password = get_password_hash(req.password)
    new_admin = {
        "email": req.email,
        "hashed_password": hashed_password,
        "full_name": req.full_name,
        "role": req.role if hasattr(req, 'role') and req.role else "admin",
        "organization_id": current_admin.organization_id,
        "created_at": datetime.now(timezone.utc),
        "allowed_features": ["dashboard", "employees", "attendance", "leaves", "expenses", "reports", "war_room", "territory", "nudge", "leaderboard"]
    }
    await admins_collection.insert_one(new_admin)
    return {"message": f"Admin {req.full_name} created successfully."}


@app.delete("/admin/sub-admins/{email}")
async def delete_sub_admin(email: str, current_admin: Admin = Depends(get_current_admin)):
    """Remove a sub-admin (Owner/Superadmin only)."""
    if current_admin.role not in ["owner", "superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Only organization owners/admins can remove admins.")
        
    if email == current_admin.email:
        raise HTTPException(status_code=400, detail="You cannot delete yourself.")
        
    # Security: Ensure we only delete admins from the same organization and who are NOT owners
    result = await admins_collection.delete_one({
        "email": email,
        "organization_id": current_admin.organization_id,
        "role": "admin" # Can only delete sub-admins, not owners
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Sub-admin not found, or you don't have permission to delete this account.")
        
    return {"message": f"Admin {email} removed successfully."}


@app.get("/admin/me")
async def get_admin_me(current_admin: Admin = Depends(get_current_admin)):
    """Return current admin's profile including role and allowed features."""
    admin_data = {
        "email": current_admin.email,
        "full_name": current_admin.full_name,
        "role": current_admin.role,
        "organization_id": current_admin.organization_id,
    }
    # owner, superadmin, and admin get all features by default
    if current_admin.role in ["owner", "superadmin", "admin"]:
        admin_data["allowed_features"] = ["dashboard", "employees", "attendance", "leaves", "expenses", "reports", "war_room", "territory", "nudge", "leaderboard", "sub_admins", "settings"]
    else:
        admin_data["allowed_features"] = current_admin.allowed_features or ["dashboard", "employees", "attendance"]
    return admin_data


@app.put("/admin/sub-admins/{email}/permissions")
async def update_sub_admin_permissions(email: str, req: dict, current_admin: Admin = Depends(get_current_admin)):
    """Update feature-level permissions for a sub-admin (Owner/Superadmin only)."""
    if current_admin.role not in ["owner", "superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Only organization owners/admins can update permissions.")
    
    allowed_features = req.get("allowed_features", [])
    role = req.get("role")  # optionally update role too
    
    update_fields = {"allowed_features": allowed_features}
    if role:
        update_fields["role"] = role
    
    result = await admins_collection.update_one(
        {"email": email, "organization_id": current_admin.organization_id},
        {"$set": update_fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Sub-admin not found.")
    
    return {"message": f"Permissions for {email} updated successfully."}


# NOTE: Duplicate /admin/stats removed. The authenticated version is at line ~2063.


@app.get("/admin/employees")
async def admin_list_employees(current_admin: Admin = Depends(get_current_admin)):
    """List all employees for management (Role Scoped)."""
    filter_query = get_employee_filter(current_admin)
    cursor = employees_collection.find(filter_query, {"hashed_password": 0, "face_embedding": 0})
    employees = await cursor.to_list(length=1000)
    for emp in employees:
        emp["_id"] = str(emp["_id"])
    return employees


@app.put("/admin/employees/{email}")
async def admin_update_employee(email: str, req: EmployeeUpdate, current_admin: Admin = Depends(get_current_admin)):
    """Update employee details."""
    update_data = {k: v for k, v in req.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")
    
    query = {"email": email}
    if current_admin.organization_id:
        query["organization_id"] = current_admin.organization_id

    result = await employees_collection.update_one(query, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    return {"message": "Employee updated successfully"}


@app.delete("/admin/employees/{email}")
async def admin_delete_employee(email: str, current_admin: Admin = Depends(get_current_admin)):
    """Remove an employee and their logs."""
    query = {"email": email}
    if current_admin.organization_id:
        query["organization_id"] = current_admin.organization_id
        
    user = await employees_collection.find_one(query)
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    # Delete logs first
    await attendance_logs_collection.delete_many({"user_id": str(user["_id"])})
    # Delete user
    await employees_collection.delete_one({"email": email})
    
    return {"message": f"Employee {email} and associated logs deleted successfully"}


@app.get("/admin/logs")
async def admin_all_logs(limit: int = 100, current_admin: Admin = Depends(get_current_admin)):
    """Fetch all attendance logs for the organization (Role Scoped)."""
    filter_query = get_employee_filter(current_admin)
    
    # If filter has manager_id, we need to find user_ids first
    if "manager_id" in filter_query:
        org_employees = await employees_collection.find(filter_query, {"_id": 1}).to_list(None)
        org_emp_ids = [str(emp["_id"]) for emp in org_employees]
        log_query = {"user_id": {"$in": org_emp_ids}}
    else:
        # For non-manager admins, we still need to filter by org usually
        # but our helper handles that mapping based on roles
        org_employees = await employees_collection.find({"organization_id": current_admin.organization_id}, {"_id": 1}).to_list(None)
        org_emp_ids = [str(emp["_id"]) for emp in org_employees]
        log_query = {"user_id": {"$in": org_emp_ids}}

    cursor = attendance_logs_collection.find(log_query).sort("timestamp", -1)
    logs = await cursor.to_list(length=limit)
    for log in logs:
        log["_id"] = str(log["_id"])
        if isinstance(log.get("timestamp"), datetime):
            log["timestamp"] = log["timestamp"].isoformat()
    return logs


@app.post("/admin/employees")
async def admin_create_employee(req: RegisterRequest, current_admin: Admin = Depends(get_current_admin)):
    """Manually register a new employee (Admin)."""
    # Check if employee already exists (globally unique email)
    existing = await employees_collection.find_one({"email": req.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Embedding is optional for manual registration if they will enroll later
    embedding = None
    if req.face_image:
        embedding = get_face_embedding(req.face_image)

    hashed_password = get_password_hash(req.password)
    employee_dict = {
        "full_name": req.full_name,
        "email": req.email,
        "employee_id": req.employee_id,
        "designation": req.designation,
        "department": req.department,
        "hashed_password": hashed_password,
        "face_embedding": embedding,
        "profile_image": req.face_image if req.face_image else None,
        "device_id": None, # Force bind on first use
        "created_at": datetime.now(timezone.utc),
        "needs_face_enrollment": True if not embedding else False,
        "organization_id": current_admin.organization_id # Bind to admin's org
    }

    await employees_collection.insert_one(employee_dict)
    return {"message": f"Employee {req.full_name} registered successfully"}


@app.post("/admin/employees/{email}/reset-password")
async def admin_reset_password(email: str, req: dict, current_admin: Admin = Depends(get_current_admin)):
    """Reset an employee's password."""
    # Ensure admin can only reset their own org's employees
    query = {"email": email}
    if current_admin.organization_id:
        query["organization_id"] = current_admin.organization_id

    user = await employees_collection.find_one(query)
    if not user:
         raise HTTPException(status_code=404, detail="Employee not found or access denied")

    new_password = req.get("password")
    if not new_password:
        raise HTTPException(status_code=400, detail="New password is required")
    
    hashed_password = get_password_hash(new_password)
    result = await employees_collection.update_one(
        {"email": email},
        {"$set": {"hashed_password": hashed_password}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    return {"message": "Password reset successfully"}


@app.post("/admin/employees/{email}/clear-binding")
async def admin_clear_binding(email: str, current_admin: Admin = Depends(get_current_admin)):
    """Clear hardware binding for an employee."""
    query = {"email": email}
    if current_admin.organization_id:
        query["organization_id"] = current_admin.organization_id

    result = await employees_collection.update_one(
        query,
        {"$set": {"device_id": None}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    return {"message": "Hardware binding cleared successfully"}


# --- HELPER FUNCTIONS ---
def get_employee_filter(current_admin: Admin):
    """Returns a MongoDB filter based on admin role for multi-tenant segmenting."""
    org_id = current_admin.organization_id
    if not org_id:
        # Fallback for superadmin without org
        return {}
    
    role = current_admin.role
    base_filter = {"organization_id": org_id}
    
    if role == "manager":
        # Managers only see their assigned team
        base_filter["manager_id"] = current_admin.email
        
    return base_filter

async def get_scoped_employee_ids(current_admin: Admin):
    base_filter = get_employee_filter(current_admin)
    emps = await employees_collection.find(base_filter, {"_id": 1}).to_list(None)
    return [str(e["_id"]) for e in emps]

async def get_scoped_employee_emails(current_admin: Admin):
    base_filter = get_employee_filter(current_admin)
    emps = await employees_collection.find(base_filter, {"email": 1}).to_list(None)
    return [e["email"] for e in emps]

async def get_scoped_employee_employee_ids(current_admin: Admin):
    base_filter = get_employee_filter(current_admin)
    emps = await employees_collection.find(base_filter, {"employee_id": 1}).to_list(None)
    return [e.get("employee_id") for e in emps if "employee_id" in e]


def check_feature_access(admin: Admin, feature: str):
    """Raises 403 if the admin doesn't have access to a specific feature."""
    if admin.role in ["owner", "superadmin"]:
        return  # Full access
    allowed = admin.allowed_features or []
    if feature not in allowed:
        raise HTTPException(status_code=403, detail=f"You do not have access to the '{feature}' feature. Contact your admin.")


@app.post("/admin/employees/bulk-assign-manager")
async def bulk_assign_manager(req: dict, current_admin: Admin = Depends(get_current_admin)):
    """Assign multiple employees to a manager by email."""
    if current_admin.role not in ["owner", "hr", "superadmin"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions to assign managers.")
    
    employee_emails = req.get("employee_emails", [])
    manager_email = req.get("manager_email")
    
    if not manager_email:
        raise HTTPException(status_code=400, detail="Manager email is required")

    org_id = current_admin.organization_id
    
    result = await employees_collection.update_many(
        {"email": {"$in": employee_emails}, "organization_id": org_id},
        {"$set": {"manager_id": manager_email}}
    )
    
    return {"message": f"Successfully assigned {result.modified_count} employees to {manager_email}."}


@app.get("/admin/settings")
async def get_settings(current_admin: Admin = Depends(get_current_admin)):
    """Retrieve organization-specific configuration."""
    org_id = current_admin.organization_id
    if not org_id:
         # Fallback to global config for superadmins or unlinked orgs
         settings = await settings_collection.find_one({"id": "config"})
         return settings or SystemSettings().dict()

    settings = await settings_collection.find_one({"organization_id": org_id})
    if not settings:
        return SystemSettings().dict()
    return settings


@app.put("/admin/settings")
async def update_settings(req: SystemSettings, current_admin: Admin = Depends(get_current_admin)):
    """Update organization-specific configuration and branding."""
    org_id = current_admin.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization linkage required for settings update.")

    update_dict = req.dict()
    # Explicitly handle logo_url to ensure it's not lost if not provided in request but exists in DB
    # However, since req is a SystemSettings model, it will have logo_url (even if None)
    
    update_dict["organization_id"] = org_id
    update_dict["updated_at"] = datetime.now(timezone.utc)

    # Persistence to settings collection
    await settings_collection.update_one(
        {"organization_id": org_id},
        {"$set": update_dict},
        upsert=True
    )

    # Branding persistence to organization collection
    branding_update = {}
    if update_dict.get("primary_color"):
        branding_update["primary_color"] = update_dict.get("primary_color")
    if update_dict.get("logo_url"):
        branding_update["logo_url"] = update_dict.get("logo_url")
    
    if branding_update:
        await organizations_collection.update_one(
            {"_id": ObjectId(org_id)},
            {"$set": branding_update}
        )

    return {"message": "Settings and branding updated successfully"}


@app.post("/admin/upload-logo")
async def admin_upload_logo(file: bytes = File(...), current_admin: Admin = Depends(get_current_admin)):
    """Upload organization logo image."""
    try:
        org_id = current_admin.organization_id
        if not org_id:
            raise HTTPException(status_code=403, detail="Organization context required")

        # Generate unique filename
        file_extension = "png" # Default, or we could extract from headers
        filename = f"logo_{org_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        with open(file_path, "wb") as buffer:
            buffer.write(file)
            
        # Generate full URL
        # In production this should be the full domain, for now relative or configured base
        base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        logo_url = f"{base_url}/uploads/{filename}"
        
        return {"logo_url": logo_url}
    except Exception as e:
        logger.error(f"Logo upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Logo upload failed: {str(e)}")


@app.get("/settings")
async def get_public_settings():
    """Public settings for mobile app (timings, etc.)."""
    settings = await settings_collection.find_one({"id": "config"})
    if not settings:
        return SystemSettings().dict()
    return settings


@app.get("/organizations/search")
async def search_organizations(q: str):
    """Public search for organizations by name or slug."""
    logger.info(f"[SEARCH] Query received: '{q}'")
    if not q or len(q) < 2:
        return []
        
    # Case-insensitive partial match
    regex_query = {"$regex": q, "$options": "i"}
    query = {
        "$or": [
            {"name": regex_query},
            {"slug": regex_query}
        ]
    }
    
    cursor = organizations_collection.find(query).limit(5)
    orgs = await cursor.to_list(length=5)
    
    results = []
    for org in orgs:
        results.append({
            "id": str(org["_id"]),
            "name": org["name"],
            "slug": org["slug"],
            "logo_url": org.get("logo_url"),
            "primary_color": org.get("primary_color")
        })
        
    return results



# -----------------------------------------------------------------------------
# FIELD SALES MODULE - /api/field/
# -----------------------------------------------------------------------------

@app.post("/api/field/plan")
async def submit_visit_plan(plan: VisitPlan, employee=Depends(get_current_employee)):
    """Submit a daily visit plan for approval."""
    try:
        # Security Enforcement: Identity Hijack Prevention
        plan_dict = plan.dict()
        plan_dict["employee_id"] = employee["email"]
        plan_dict["organization_id"] = employee["organization_id"]
        
        # Check if plan already exists for this date
        existing = await visit_plans_collection.find_one({
            "employee_id": employee["email"],
            "date": plan.date
        })
        
        if existing:
            await visit_plans_collection.delete_one({"_id": existing["_id"]})
        
        plan_dict["status"] = PlanStatus.SUBMITTED
        plan_dict["submitted_at"] = datetime.now(timezone.utc)
        
        result = await visit_plans_collection.insert_one(plan_dict)
        return {"status": "success", "message": "Visit plan submitted for manager approval", "plan_id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Failed to submit plan: {e}")
        raise HTTPException(status_code=500, detail="Plan submission failed")


@app.get("/api/field/plan/{employee_id}")
async def get_current_plan(employee_id: str, date: Optional[str] = None, current_user=Depends(get_current_employee)):
    """Retrieve the active (approved) plan for today."""
    # Privacy Enforcement: Can only see own plan (or admin if we added that check)
    if employee_id != current_user["email"]:
        raise HTTPException(status_code=403, detail="Access denied: Cannot view another agent's plan.")
        
    query_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan = await visit_plans_collection.find_one({
        "employee_id": employee_id,
        "date": query_date,
        "status": PlanStatus.APPROVED
    })
    
    if not plan:
        # Check for submitted/draft if no approved one exists
        plan = await visit_plans_collection.find_one({
            "employee_id": employee_id,
            "date": query_date
        })
        
    if not plan:
        return {"status": "no_plan", "message": "No visit plan found for today."}
    
    plan["_id"] = str(plan["_id"])

    # Enrichment: Inject Status (completed, ongoing, pending) into stops
    # fetch all logs for this agent today
    visit_logs = await visit_logs_collection.find({
        "employee_id": employee_id,
        "date": query_date
    }).to_list(length=100)

    # Map stop_id to status
    status_map = {}
    for log in visit_logs:
        stop_id = log.get("visit_plan_stop_id")
        if stop_id:
            status_map[stop_id] = log.get("status", "completed")

    for stop in plan.get("stops", []):
        stop_id = stop.get("visit_id") # frontend sends visit_id as stop identifier
        stop["status"] = status_map.get(stop_id, "pending")

    return plan


@app.post("/api/field/plan/optimize")
async def optimize_route(req: dict, employee=Depends(get_current_employee)):
    """Optimize the order of stops in today's approved plan using Nearest Neighbor TSP."""
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        plan = await visit_plans_collection.find_one({
            "employee_id": employee["email"],
            "date": today_str,
            "status": {"$in": [PlanStatus.APPROVED, "approved"]}
        })
        if not plan:
            raise HTTPException(status_code=404, detail="No approved plan for today")

        stops = plan.get("stops", [])
        if len(stops) <= 1:
            return {"status": "success", "message": "No optimization needed", "stops": stops}

        # Handle current coordinates safely
        curr_lat = req.get("current_lat")
        curr_lng = req.get("current_lng")
        
        # If no current coords provided, use the first stop's coords as starting point
        if curr_lat is None or curr_lng is None:
            starts_lat = float(stops[0].get("place_lat", 0))
            starts_lng = float(stops[0].get("place_lng", 0))
        else:
            starts_lat = float(curr_lat)
            starts_lng = float(curr_lng)

        # Nearest Neighbor Algorithm
        unvisited = list(range(len(stops)))
        current_lat, current_lng = starts_lat, starts_lng
        ordered_indices = []

        while unvisited:
            nearest_idx = -1
            nearest_dist = float("inf")
            for idx in unvisited:
                stop = stops[idx]
                slat = float(stop.get("place_lat", 0))
                slng = float(stop.get("place_lng", 0))
                # Skip invalid coords
                if slat == 0 and slng == 0: continue
                
                dist = calculate_haversine(current_lat, current_lng, slat, slng)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_idx = idx
            
            if nearest_idx == -1: # Fallback if all remaining have invalid coords
                nearest_idx = unvisited[0]
                
            ordered_indices.append(nearest_idx)
            current_lat = float(stops[nearest_idx].get("place_lat", 0))
            current_lng = float(stops[nearest_idx].get("place_lng", 0))
            unvisited.remove(nearest_idx)

        optimized_stops = []
        for i, idx in enumerate(ordered_indices):
            stop = stops[idx]
            stop["sequence_order"] = i + 1 # 1-indexed
            optimized_stops.append(stop)

        # Persist updated order
        await visit_plans_collection.update_one(
            {"_id": plan["_id"]},
            {"$set": {"stops": optimized_stops}}
        )

        return {"status": "success", "stops": optimized_stops}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Route optimization failed: {e}")
        raise HTTPException(status_code=500, detail=f"Route optimization failed: {str(e)}")


@app.post("/api/field/visit/check-in")
async def visit_check_in(req: dict, employee=Depends(get_current_employee)):
    """Log a site check-in at a specific client/location with geofence validation."""
    try:
        import math
        # Mock Location Prevention
        if req.get("mock_detected"):
             logger.warning(f"Field Security Alert: Mock GPS detected during check-in for {employee['email']}")
             await trigger_alert(
                 "Territory", 
                 employee["email"], 
                 employee["organization_id"], 
                 "Mock GPS detected during visit check-in attempt.", 
                 "high",
                 {"lat": agent_lat, "lng": agent_lng, "place": req.get("place_name")}
             )
             raise HTTPException(status_code=403, detail="Security Violation: Mock Location detected. Check-in rejected.")

        agent_lat = float(req["lat"])
        agent_lng = float(req["lng"])
        geofence_validated = False
        geofence_distance = None

        # --- GEOFENCE VALIDATION (100m radius) ---
        stop_id = req.get("stop_id")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        plan = await visit_plans_collection.find_one({
            "employee_id": employee["email"],
            "date": today_str,
            "status": PlanStatus.APPROVED
        })

        if plan and stop_id is not None:
            # Find the matching stop in the plan
            target_stop = None
            for stop in plan.get("stops", []):
                if stop.get("sequence_order") == stop_id or str(stop.get("sequence_order")) == str(stop_id):
                    target_stop = stop
                    break

            if target_stop and target_stop.get("place_lat") and target_stop.get("place_lng"):
                stop_lat = float(target_stop["place_lat"])
                stop_lng = float(target_stop["place_lng"])
                # Haversine distance
                dlat = math.radians(agent_lat - stop_lat)
                dlon = math.radians(agent_lng - stop_lng)
                a = math.sin(dlat / 2)**2 + math.cos(math.radians(stop_lat)) * math.cos(math.radians(agent_lat)) * math.sin(dlon / 2)**2
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                geofence_distance = round(6371000 * c, 1)  # meters

                GEOFENCE_RADIUS = 100  # meters
                if geofence_distance > GEOFENCE_RADIUS:
                    await trigger_alert(
                        "Compliance", 
                        employee["email"], 
                        employee["organization_id"], 
                        f"Geofence Breach: Agent is {geofence_distance:.0f}m away from planned stop '{target_stop['place_name']}'.", 
                        "medium",
                        {"lat": agent_lat, "lng": agent_lng, "distance": geofence_distance, "stop_id": stop_id}
                    )
                    raise HTTPException(
                        status_code=403,
                        detail=f"You are {geofence_distance:.0f}m away from {target_stop['place_name']}. Must be within {GEOFENCE_RADIUS}m to check in."
                    )
                geofence_validated = True

        # --- Save selfie photo if provided ---
        selfie_url = None
        face_verified = False
        if req.get("selfie_base64"):
            import uuid as _uuid
            os.makedirs("uploads/selfies", exist_ok=True)
            fname = f"selfies/checkin_{employee['email'].replace('@','_')}_{_uuid.uuid4().hex[:8]}.jpg"
            with open(f"uploads/{fname}", "wb") as f:
                f.write(base64.b64decode(req["selfie_base64"]))
            selfie_url = f"/uploads/{fname}"

            # Optional face verification against stored descriptors
            try:
                user = await employees_collection.find_one({"email": employee["email"]})
                if user and user.get("face_descriptor"):
                    from auth import verify_face
                    match = verify_face(req["selfie_base64"], user["face_descriptor"])
                    face_verified = match
                    if not match:
                        await trigger_alert(
                            "Identity",
                            employee["email"],
                            employee["organization_id"],
                            f"Face mismatch during visit check-in at '{req.get('place_name', 'Unknown')}'.",
                            "high",
                            {"lat": agent_lat, "lng": agent_lng, "place": req.get("place_name")}
                        )
            except Exception as face_err:
                logger.warning(f"Face verification skipped during check-in: {face_err}")

        log = {
            "employee_id": employee["email"],
            "organization_id": employee["organization_id"],
            "date": today_str,
            "check_in_time": datetime.now(timezone.utc),
            "check_in_lat": agent_lat,
            "check_in_lng": agent_lng,
            "check_in_accuracy": req.get("accuracy", 0),
            "place_name": target_stop.get("place_name") if (target_stop and target_stop.get("place_name")) else req.get("place_name", "Unknown"),
            "visit_plan_stop_id": stop_id,
            "geofence_validated": geofence_validated,
            "geofence_distance_meters": geofence_distance,
            "selfie_url": selfie_url,
            "face_verified": face_verified,
            "status": "ongoing"
        }
        
        result = await visit_logs_collection.insert_one(log)
        return {"status": "success", "visit_id": str(result.inserted_id), "geofence_validated": geofence_validated, "distance_meters": geofence_distance}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Check-in failed: {e}")
        raise HTTPException(status_code=500, detail="Visit check-in failed")


@app.post("/api/field/visit/check-out")
async def visit_check_out(req: dict, background_tasks: BackgroundTasks, employee=Depends(get_current_employee)):
    """Log check-out with remarks, media, person met, and outcome."""
    try:
        # Security: Verify that this visit belongs to the organization
        visit = await visit_logs_collection.find_one({"_id": ObjectId(req["visit_id"])})
        if not visit or visit["organization_id"] != employee["organization_id"]:
             raise HTTPException(status_code=403, detail="Access denied: Visit record not found or cross-org violation.")

        # --- Optional Face Verification for Check-out ---
        face_verified = True
        if req.get("selfie_base64"):
            try:
                # Get employee's enrolled descriptor
                target_descriptor = employee.get("face_descriptor")
                if target_descriptor:
                    # Verify provided selfie
                    is_match, score = verify_face(req["selfie_base64"], target_descriptor)
                    face_verified = is_match
                    if not is_match:
                        # Alert admin but don't block check-out (could be lighting etc)
                        await send_security_alert_notification(
                            "VISIT_FACE_MISMATCH", 
                            employee["email"], 
                            f"Face mismatch during check-out for visit {req['visit_id']}. Confidence: {score}"
                        )
                else:
                    logger.warning(f"Employee {employee['email']} has no enrolled face for verification.")
            except Exception as e:
                logger.error(f"Face verification during check-out failed: {e}")
                face_verified = False

        # --- Save voice note if provided ---
        voice_note_url = None
        if req.get("voice_note_base64"):
            import uuid as _uuid
            os.makedirs("uploads/voice_notes", exist_ok=True)
            fname = f"voice_notes/visit_{req['visit_id']}_{_uuid.uuid4().hex[:8]}.m4a"
            with open(f"uploads/{fname}", "wb") as f:
                f.write(base64.b64decode(req["voice_note_base64"]))
            voice_note_url = f"/uploads/{fname}"

        # --- Save site photo if provided ---
        site_photo_url = None
        if req.get("site_photo_base64"):
            import uuid as _uuid
            os.makedirs("uploads/site_photos", exist_ok=True)
            fname = f"site_photos/visit_{req['visit_id']}_{_uuid.uuid4().hex[:8]}.jpg"
            with open(f"uploads/{fname}", "wb") as f:
                f.write(base64.b64decode(req["site_photo_base64"]))
            site_photo_url = f"/uploads/{fname}"

        update_data = {
            "check_out_time": datetime.now(timezone.utc),
            "check_out_lat": req["lat"],
            "check_out_lng": req["lng"],
            "remarks": req.get("remarks"),
            "outcome": req.get("outcome"),
            "order_captured": req.get("order_captured", False),
            "lead_captured": req.get("lead_captured", False),
            "lead_details": req.get("lead_details"),
            "person_met_name": req.get("person_met_name"),
            "person_met_role": req.get("person_met_role"),
            "voice_note_url": voice_note_url,
            "site_photo_url": site_photo_url,
            "face_verified_checkout": face_verified,
            "status": "completed"
        }
        
        await visit_logs_collection.update_one(
            {"_id": ObjectId(req["visit_id"])},
            {"$set": update_data}
        )

        # Trigger Background Sync to Google Sheets for Visit Data
        background_tasks.add_task(sync_visit_to_google_sheets, {**visit, **update_data})

        return {"status": "success", "message": "Visit completed"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Check-out failed: {e}")
        raise HTTPException(status_code=500, detail="Visit check-out failed")


@app.post("/api/field/ping")
async def receive_location_ping(ping: LocationPing, employee=Depends(get_current_employee)):
    """Receive background GPS breadcrumb. Strictly restricted to active duty window."""
    try:
        # Privacy Check: Verify agent is currently checked-in
        # We look for the latest attendance log for this user today
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        
        last_log = await attendance_logs_collection.find_one(
            {"user_id": str(employee["_id"]), "timestamp": {"$gte": start_of_day}},
            sort=[("timestamp", -1)]
        )
        
        # Only record if last log was a "check-in" (meaning they haven't checked out yet)
        if not last_log or last_log.get("type") != "check-in":
            return {"status": "ignored", "reason": "privacy_filter_active_duty_only"}

        ping_dict = ping.dict()
        ping_dict["employee_id"] = employee["email"]
        ping_dict["organization_id"] = employee["organization_id"]
        ping_dict["recorded_at"] = now
        
        await location_pings_collection.insert_one(ping_dict)

        # --- STATIONARY FRAUD DETECTION ---
        # Fetch last 5 pings to check for movement
        last_pings = await location_pings_collection.find(
            {"employee_id": employee["email"]},
            sort=[("recorded_at", -1)]
        ).to_list(length=6) # Get 6 to compare against the one just inserted

        if len(last_pings) >= 6:
            # Check if all 6 pings have identical lat/lng (within 0.00001 precision)
            first_p = last_pings[0]
            is_stationary = True
            for i in range(1, 6):
                p = last_pings[i]
                if abs(p["lat"] - first_p["lat"]) > 0.0001 or abs(p["lng"] - first_p["lng"]) > 0.0001:
                    is_stationary = False
                    break
            
            if is_stationary:
                # Only trigger if not already flagged in the last hour
                recent_alert = await alerts_collection.find_one({
                    "employee_id": employee["email"],
                    "type": "Productivity",
                    "timestamp": {"$gte": datetime.now(timezone.utc) - timedelta(hours=1)}
                })
                if not recent_alert:
                    await trigger_alert(
                        "Productivity",
                        employee["email"],
                        employee["organization_id"],
                        "Stationary Fraud: Agent has been at the exact same coordinates for last 6 pings (>1 hour).",
                        "medium",
                        {"lat": first_p["lat"], "lng": first_p["lng"]}
                    )

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Ping record failed: {e}")
        return {"status": "error"}

@app.post("/api/field/sync/batch")
async def sync_offline_batch(req: SyncBatchRequest, background_tasks: BackgroundTasks, employee=Depends(get_current_employee)):
    """Synchronize offline data back to the server in a single batch to save bandwidth and handle reconnects."""
    try:
        now = datetime.now(timezone.utc)
        synced_count = {"attendance": 0, "visits": 0, "pings": 0}

        # 1. Sync Attendance Logs
        for att in req.attendance_logs:
            offline_id = att.get("offline_id")
            if not offline_id:
                continue
            exists = await attendance_logs_collection.find_one({"offline_id": offline_id})
            if not exists:
                att["synced_at"] = now
                # Hard link to current user to prevent tampering
                att["user_id"] = str(employee["_id"]) 
                att["organization_id"] = employee["organization_id"]
                # Convert string timestamp to datetime if needed
                if "timestamp" in att and isinstance(att["timestamp"], str):
                    try:
                        att["timestamp"] = datetime.fromisoformat(att["timestamp"].replace("Z", "+00:00"))
                    except:
                        pass
                await attendance_logs_collection.insert_one(att)
                synced_count["attendance"] += 1

        # 2. Sync Visits
        for visit in req.visits:
            offline_id = visit.get("offline_id")
            if not offline_id:
                continue
            exists = await visit_logs_collection.find_one({"offline_id": offline_id})
            if not exists:
                visit["synced_at"] = now
                visit["employee_id"] = employee["email"]
                visit["organization_id"] = employee["organization_id"]
                
                # Convert timestamps
                for date_field in ["check_in_time", "check_out_time"]:
                    if date_field in visit and isinstance(visit[date_field], str):
                        try:
                            visit[date_field] = datetime.fromisoformat(visit[date_field].replace("Z", "+00:00"))
                        except:
                            pass
                
                # Check for base64 media to save
                if visit.get("site_photo_base64"):
                    import uuid as _uuid
                    os.makedirs("uploads/site_photos", exist_ok=True)
                    fname = f"site_photos/sync_{offline_id}_{_uuid.uuid4().hex[:8]}.jpg"
                    with open(f"uploads/{fname}", "wb") as f:
                        f.write(base64.b64decode(visit["site_photo_base64"]))
                    visit["site_photo_url"] = f"/uploads/{fname}"

                await visit_logs_collection.insert_one(visit)
                
                # Trigger Sheets sync
                background_tasks.add_task(sync_visit_to_google_sheets, visit)
                synced_count["visits"] += 1

        # 3. Sync Pings
        for ping in req.pings:
            offline_id = ping.get("offline_id")
            if not offline_id:
                continue
            exists = await location_pings_collection.find_one({"offline_id": offline_id})
            if not exists:
                ping["synced_at"] = now
                ping["employee_id"] = employee["email"]
                ping["organization_id"] = employee["organization_id"]
                if "recorded_at" in ping and isinstance(ping["recorded_at"], str):
                    try:
                        ping["recorded_at"] = datetime.fromisoformat(ping["recorded_at"].replace("Z", "+00:00"))
                    except:
                        pass
                await location_pings_collection.insert_one(ping)
                synced_count["pings"] += 1

        return {"status": "success", "synced": synced_count}

    except Exception as e:
        logger.error(f"Batch sync failed: {e}")
        raise HTTPException(status_code=500, detail="Batch synchronization failed")


@app.get("/api/field/km-suggestion")
async def get_km_suggestion(employee=Depends(get_current_employee)):
    """Calculate suggested KM for today based on active duty pings."""
    try:
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        
        pings = await location_pings_collection.find({
            "employee_id": employee["email"],
            "recorded_at": {"$gte": start_of_day}
        }).sort("recorded_at", 1).to_list(length=2000)
        
        total_km = 0.0
        if len(pings) > 1:
            for i in range(len(pings) - 1):
                p1 = pings[i]
                p2 = pings[i+1]
                dist_meters = calculate_haversine(p1["lat"], p1["lng"], p2["lat"], p2["lng"])
                total_km += (dist_meters / 1000.0)
                
        return {"suggested_km": round(total_km, 2)}
    except Exception as e:
        logger.error(f"KM Suggestion failed: {e}")
        return {"suggested_km": 0.0}

@app.post("/api/field/reimbursement/claim")
async def submit_km_reimbursement(req: dict, employee=Depends(get_current_employee)):
    """Submit a formal KM reimbursement claim based on suggested KM."""
    try:
        date_str = req.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total_km = float(req.get("total_km", 0))
        
        # Check if already claimed for this date
        existing = await km_reimbursements_collection.find_one({
            "employee_id": employee["email"],
            "date": date_str
        })
        if existing:
            raise HTTPException(status_code=400, detail=f"KM reimbursement already claimed for {date_str}")
        
        # Default rate (could be dynamic later)
        rate_per_km = 10.0 # Example: 10 INR per KM
        
        claim = {
            "employee_id": employee["email"],
            "organization_id": employee["organization_id"],
            "date": date_str,
            "total_km": total_km,
            "rate_per_km": rate_per_km,
            "total_amount": total_km * rate_per_km,
            "status": "pending",
            "created_at": datetime.now(timezone.utc)
        }
        
        result = await km_reimbursements_collection.insert_one(claim)
        return {"status": "success", "claim_id": str(result.inserted_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit KM claim: {e}")
        raise HTTPException(status_code=500, detail="KM reimbursement claim failed")

@app.get("/admin/field/reimbursements")
async def admin_list_km_claims(status: Optional[str] = "pending", admin=Depends(get_current_admin)):
    """List KM reimbursement claims for approval."""
    query = {}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id
    if status:
        query["status"] = status
        
    claims = await km_reimbursements_collection.find(query).sort("created_at", -1).to_list(length=200)
    for c in claims:
        c["_id"] = str(c["_id"])
        if isinstance(c.get("created_at"), datetime):
            c["created_at"] = c["created_at"].isoformat()
        # Enrich with employee name
        emp = await employees_collection.find_one({"email": c["employee_id"]})
        c["full_name"] = emp["full_name"] if emp else "Unknown"
        
    return claims

@app.post("/admin/field/reimbursements/{claim_id}/{action}")
async def process_km_reimbursement(claim_id: str, action: str, admin=Depends(get_current_admin)):
    "Approve or Reject a KM reimbursement claim."
    new_status = "approved" if action == "approve" else "rejected"
    
    query = {"_id": ObjectId(claim_id)}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id

    update_data = {
        "status": new_status,
        "approved_by": admin.email,
        "approved_at": datetime.now(timezone.utc)
    }
    
    result = await km_reimbursements_collection.update_one(
        query,
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Claim not found or access denied")
        
    return {"status": "success"}

@app.get("/admin/field/trail/{employee_email}")
async def get_agent_trail(employee_email: str, admin=Depends(get_current_admin)):
    """Fetch today's location trail for a specific agent (Admin only)."""
    try:
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        
        base_filter = get_employee_filter(admin)
        emp_query = {"email": employee_email, **base_filter}
        
        emp = await employees_collection.find_one(emp_query)
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found or unauthorized")

        pings = await location_pings_collection.find({
            "employee_id": employee_email,
            "recorded_at": {"$gte": start_of_day}
        }).sort("recorded_at", 1).to_list(length=2000)
        
        trail = [[p["lat"], p["lng"]] for p in pings]
        return {"trail": trail}
    except Exception as e:
        logger.error(f"Trail fetch failed: {e}")
        return {"trail": []}




@app.get("/api/field/summary/{employee_id}")
async def get_field_day_summary(employee_id: str, date: Optional[str] = None, current_user=Depends(get_current_employee)):
    """Get summarized KM and Visit count for the day."""
    # Privacy Enforcement
    if employee_id != current_user["email"]:
        raise HTTPException(status_code=403, detail="Access denied: Cannot view another agent's summary.")
        
    query_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # 1. Total Visits
    visits = await visit_logs_collection.count_documents({
        "employee_id": employee_id,
        "date": query_date,
        "status": "completed"
    })
    
    # 2. Daily KM Calculation (Simplified from pings)
    pings = await location_pings_collection.find({
        "employee_id": employee_id,
        "recorded_at": {
            "$gte": datetime.strptime(query_date, "%Y-%m-%d"),
            "$lt": datetime.strptime(query_date, "%Y-%m-%d") + timedelta(days=1)
        }
    }).sort("recorded_at", 1).to_list(length=1000)
    
    total_km = 0
    if len(pings) > 1:
        for i in range(len(pings) - 1):
            p1 = pings[i]
            p2 = pings[i+1]
            dist = calculate_haversine(p1["lat"], p1["lng"], p2["lat"], p2["lng"])
            total_km += (dist / 1000)
            
    return {
        "date": query_date,
        "total_visits": visits,
        "total_km": round(total_km, 2),
        "status": "Active"
    }


# -----------------------------------------------------------------------------
# ADMIN COMMAND CENTER - /admin/field/
# -----------------------------------------------------------------------------


@app.put("/admin/employees/{email}/territory")
async def update_territory(email: str, req: dict, admin=Depends(get_current_admin)):
    """Update geofence/territory for a specific agent. Supports both radius and polygon."""
    # Build org-scoped query
    query = {"email": email}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id

    update_fields = {
        "territory_type": req.get("territory_type", "radius"),
    }

    if req.get("territory_type") == "polygon":
        update_fields["territory_polygon"] = req.get("territory_polygon", [])
        # Clear radius fields when switching to polygon
        update_fields["territory_center_lat"] = None
        update_fields["territory_center_lng"] = None
        update_fields["territory_radius_meters"] = None
    else:
        update_fields["territory_center_lat"] = req.get("territory_center_lat")
        update_fields["territory_center_lng"] = req.get("territory_center_lng")
        update_fields["territory_radius_meters"] = req.get("territory_radius_meters")
        # Clear polygon fields when switching to radius
        update_fields["territory_polygon"] = None

    result = await employees_collection.update_one(query, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found or access denied")
    return {"status": "success", "message": f"Territory updated to {req.get('territory_type', 'radius')} for {email}"}


@app.get("/admin/field/visit-plans")
async def get_plans_for_approval(status: str = "submitted", admin=Depends(get_current_admin)):
    """List visit plans pending approval."""
    plans = await visit_plans_collection.find({"status": status}).to_list(length=100)
    
    # Enrichment: Add employee names
    enriched_plans = []
    for plan in plans:
        emp = await employees_collection.find_one({"employee_id": plan["employee_id"]})
        plan["_id"] = str(plan["_id"])
        plan["full_name"] = emp["full_name"] if emp else "Unknown"
        enriched_plans.append(plan)
        
    return enriched_plans


@app.post("/admin/field/visit-plans/{plan_id}/{action}")
async def process_visit_plan(plan_id: str, action: str, admin=Depends(get_current_admin)):
    """Approve or Reject a visit plan."""
    new_status = PlanStatus.APPROVED if action == "approve" else PlanStatus.REJECTED
    result = await visit_plans_collection.update_one(
        {"_id": ObjectId(plan_id)},
        {"$set": {"status": new_status, "processed_at": datetime.now(timezone.utc)}}
    )
    return {"status": "success"}

@app.put("/admin/field/visit-plans/{plan_id}")
async def update_visit_plan(plan_id: str, req: dict, admin=Depends(get_current_admin)):
    """Update a visit plan (reorder stops, edit details, add comments) before or after approval."""
    try:
        # Extract stops and comments from request
        stops = req.get("stops")
        comments = req.get("manager_comments")
        
        update_data = {}
        if stops is not None:
            update_data["stops"] = stops
        if comments is not None:
            update_data["manager_comments"] = comments
            update_data["reviewed_at"] = datetime.now(timezone.utc)
            update_data["reviewed_by"] = admin.email

        result = await visit_plans_collection.update_one(
            {"_id": ObjectId(plan_id)},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Plan not found")
            
        return {"status": "success", "message": "Plan updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/field/generate-otp/{employee_id}")
async def generate_attendance_otp(employee_id: str, admin=Depends(get_current_admin)):
    """Generate a 4-digit OTP for an employee to use as a GPS fallback."""
    import random
    otp = str(random.randint(1000, 9999))
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    # Update employee record with OTP
    result = await employees_collection.update_one(
        {"email": employee_id, "organization_id": admin.organization_id},
        {"$set": {"gps_otp": otp, "gps_otp_expiry": expiry}}
    )
    
    if result.matched_count == 0:
        # Try finding by employee_id field if email match failed
        result = await employees_collection.update_one(
            {"employee_id": employee_id, "organization_id": admin.organization_id},
            {"$set": {"gps_otp": otp, "gps_otp_expiry": expiry}}
        )
        
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found in your organization")
        
    return {"status": "success", "otp": otp, "expires_at": expiry.isoformat()}

@app.get("/admin/stats")
async def get_admin_stats(admin: dict = Depends(get_current_admin)):
    """Summary statistics for the dashboard (Role Scoped)."""
    filter_query = get_employee_filter(admin)
    
    total_employees = await employees_collection.count_documents(filter_query)
    
    # Today's start in UTC
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Get relevant employee IDs
    org_employees = await employees_collection.find(filter_query, {"_id": 1}).to_list(None)
    org_emp_ids = [str(emp["_id"]) for emp in org_employees]
    
    logs_query = {
        "timestamp": {"$gte": today_start},
        "type": "check-in",
        "user_id": {"$in": org_emp_ids}
    }
         
    clocked_in_today = await attendance_logs_collection.count_documents(logs_query)
    
    # Alerts & Fraud Detection Stats
    alert_query = {
        "organization_id": admin.organization_id, 
        "timestamp": {"$gte": today_start},
        "employee_id": {"$in": [e.get("email") for e in org_employees]} if "manager_id" in filter_query else {"$exists": True}
    }
    total_alerts_today = await alerts_collection.count_documents(alert_query)
    critical_alerts_today = await alerts_collection.count_documents({**alert_query, "severity": "critical"})
    pending_alerts = await alerts_collection.count_documents({**alert_query, "status": "pending"})

    # Real on_leave count: approved leaves covering today
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    leave_query = {
        "organization_id": admin.organization_id,
        "status": "approved",
        "start_date": {"$lte": today_str},
        "end_date": {"$gte": today_str}
    }
    if "manager_id" in filter_query:
        # Only count leaves for my team
        leave_query["employee_email"] = {"$in": [e.get("email") for e in org_employees]}

    on_leave_count = await leave_requests_collection.count_documents(leave_query)

    return {
        "total_employees": total_employees,
        "clocked_in_today": clocked_in_today,
        "late_arrivals_today": total_alerts_today, 
        "on_leave": on_leave_count,
        "avg_hours": 8.5,
        "alerts_today": total_alerts_today,
        "critical_alerts": critical_alerts_today,
        "pending_alerts": pending_alerts
    }


# -----------------------------------------------------------------------------
# PHASE 4: LEAVE/OD & DISCUSSION SYSTEM
# -----------------------------------------------------------------------------

@app.post("/api/leave/request")
async def create_leave_request(req: dict, employee=Depends(get_current_employee)):
    """Submit a new Leave or On-Duty (OD) request with optional proof (base64 image)."""
    try:
        proof_file_url = None
        if req.get("proof_url") and req["proof_url"].startswith("data:image"):
            # Handle base64 image upload
            try:
                import base64
                import uuid
                
                # Ensure proofs directory exists
                proofs_dir = os.path.join(UPLOAD_DIR, "proofs")
                if not os.path.exists(proofs_dir):
                    os.makedirs(proofs_dir)
                
                header, encoded = req["proof_url"].split(",", 1)
                ext = header.split("/")[1].split(";")[0]
                filename = f"proof_{uuid.uuid4()}.{ext}"
                file_path = os.path.join(proofs_dir, filename)
                
                with open(file_path, "wb") as f:
                    f.write(base64.b64decode(encoded))
                
                proof_file_url = f"/uploads/proofs/{filename}"
                logger.info(f"Proof uploaded and saved to {proof_file_url}")
            except Exception as upload_err:
                logger.error(f"Failed to process proof upload: {upload_err}")
                # We'll continue without the proof if it fails, or we could raise an error
        
        new_request = {
            "employee_id": employee["email"],
            "organization_id": employee.get("organization_id", "system_org"),
            "leave_type": req["leave_type"], # sick, casual, on_duty, other
            "start_date": req["start_date"],
            "end_date": req["end_date"],
            "reason": req["reason"],
            "status": "pending",
            "proof_url": proof_file_url or req.get("proof_url"),
            "discussion": [],
            "created_at": datetime.now(timezone.utc)
        }
        result = await leave_requests_collection.insert_one(new_request)
        return {"status": "success", "request_id": str(result.inserted_id), "proof_url": proof_file_url}
    except Exception as e:
        logger.error(f"Failed to create leave request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to submit request")

@app.get("/api/leave/my-requests")
async def get_my_leave_requests(employee=Depends(get_current_employee)):
    """List current employee's leave requests."""
    requests = await leave_requests_collection.find({"employee_id": employee["email"]}).sort("created_at", -1).to_list(length=100)
    for r in requests:
        r["_id"] = str(r["_id"])
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
    return requests

# --- Manager Endpoints ---
@app.get("/api/manager/team-attendance")
async def get_team_attendance(manager=Depends(get_current_employee)):
    """Fetch current attendance status for all subordinates."""
    cursor = employees_collection.find({"manager_id": manager["email"]})
    subordinates = await cursor.to_list(length=100)
    
    results = []
    for sub in subordinates:
        # Get latest attendance log
        last_log = await attendance_logs_collection.find_one(
            {"user_id": str(sub["_id"])},
            sort=[("timestamp", -1)]
        )
        
        status = "check-out"
        last_time = "N/A"
        if last_log:
            status = last_log["type"]
            ts = last_log["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            last_time = ts.strftime("%I:%M %p")
            
        results.append({
            "id": str(sub["_id"]),
            "full_name": sub.get("full_name", "Unknown"),
            "email": sub["email"],
            "status": status,
            "last_time": last_time
        })
    return results

@app.get("/api/manager/pending-leaves")
async def get_pending_leaves(manager=Depends(get_current_employee)):
    """Fetch pending leave requests from my team members."""
    subordinates = await employees_collection.find({"manager_id": manager["email"]}).to_list(length=100)
    sub_emails = [s["email"] for s in subordinates]
    
    cursor = leave_requests_collection.find({
        "employee_id": {"$in": sub_emails},
        "status": "pending"
    }).sort("created_at", -1)
    
    requests = await cursor.to_list(length=100)
    results = []
    for req in requests:
        emp = next((s for s in subordinates if s["email"] == req["employee_id"]), None)
        emp_name = emp["full_name"] if emp else req["employee_id"]
        
        results.append({
            "id": str(req["_id"]),
            "full_name": emp_name,
            "employee_id": req["employee_id"],
            "reason": req["reason"],
            "start_date": req["start_date"],
            "end_date": req["end_date"],
            "status": req["status"],
            "proof_url": req.get("proof_url")
        })
    return results

@app.post("/api/leave/request/{request_id}/approve")
async def manager_approve_leave(request_id: str, payload: dict, manager=Depends(get_current_employee)):
    """Allow a manager to approve/reject leave for their team."""
    status = payload.get("status")
    if status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status. Use 'approved' or 'rejected'.")
        
    leave_req = await leave_requests_collection.find_one({"_id": ObjectId(request_id)})
    if not leave_req:
        raise HTTPException(status_code=404, detail="Leave request not found")
        
    emp = await employees_collection.find_one({"email": leave_req["employee_id"], "manager_id": manager["email"]})
    if not emp:
         raise HTTPException(status_code=403, detail="Forbidden: You can only manage leaves for your direct reports.")
         
    await leave_requests_collection.update_one(
        {"_id": ObjectId(request_id)},
        {"$set": {
            "status": status,
            "processed_at": datetime.now(timezone.utc),
            "processed_by": manager["email"]
        }}
    )
    return {"status": "success", "new_status": status}

@app.get("/admin/leave/requests")
async def admin_get_leave_requests(status: Optional[str] = None, admin: dict = Depends(get_current_admin)):
    """List all leave requests for the organization (Role Scoped)."""
    filter_query = get_employee_filter(admin)
    
    # Map get_employee_filter logic to leave requests
    org_id = admin.organization_id
    leave_query = {}
    if org_id:
        leave_query["organization_id"] = org_id
        
    if admin.role == "manager":
        # Find all employees for this manager
        org_employees = await employees_collection.find({"organization_id": org_id, "manager_id": admin.email}, {"email": 1}).to_list(None)
        emp_emails = [e.get("email") for e in org_employees if e.get("email")]
        leave_query["employee_id"] = {"$in": emp_emails}
        
    if status:
        leave_query["status"] = status
        
    requests = await leave_requests_collection.find(leave_query).sort("created_at", -1).to_list(length=100)
    enriched = []
    for r in requests:
        r["_id"] = str(r["_id"])
        emp_id = r.get("employee_id")
        if emp_id:
            emp = await employees_collection.find_one({"email": emp_id})
            r["full_name"] = emp.get("full_name", emp.get("name", "Unknown")) if emp else "Unknown"
        else:
            r["full_name"] = "Unknown"
            
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
        enriched.append(r)
    return enriched

async def get_current_any_user(token: str = Depends(employee_oauth2_scheme)):
    """Try to decode as employee first, then admin."""
    try:
        user = await get_current_employee(token)
        if user:
            return user, "employee"
    except Exception:
        pass
    
    try:
        admin = await get_current_admin(token)
        if admin:
            return admin, "admin"
    except Exception:
        pass
        
    raise HTTPException(status_code=401, detail="Invalid session")

@app.get("/api/leave/requests/{request_id}/discussion")
async def get_leave_discussion(request_id: str, auth_data=Depends(get_current_any_user)):
    """Fetch discussion/chat history for a leave request."""
    try:
        obj_id = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID")

    req = await leave_requests_collection.find_one({"_id": obj_id})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
        
    user, role = auth_data
    # Access check: Admin of same org or the employee themselves
    if role == "admin":
        admin_org = user.organization_id
        # Global superadmin (no org_id) can see all
        if admin_org and admin_org != req.get("organization_id"):
             raise HTTPException(status_code=403, detail="Access denied")
    else:
        if user.get("email") != req.get("employee_id"):
             raise HTTPException(status_code=403, detail="Access denied")

    return req.get("discussion", [])

@app.post("/api/leave/requests/{request_id}/message")
async def post_leave_message(request_id: str, payload: dict, auth_data=Depends(get_current_any_user)):
    """Post a new message to the leave request discussion (Chat). Supports both Admin and Employee tokens."""
    try:
        obj_id = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID")
        
    if not payload or "message" not in payload:
        raise HTTPException(status_code=400, detail="Message content is required")
        
    user, role = auth_data
    
    if role == "admin":
        sender_id = user.email
        sender_name = user.full_name
    else:
        sender_id = user.get("email")
        sender_name = user.get("full_name", user.get("name", "Unknown"))

    message = {
        "sender_id": sender_id,
        "sender_name": sender_name,
        "role": role,
        "message": payload["message"],
        "timestamp": datetime.now(timezone.utc)
    }
    
    result = await leave_requests_collection.update_one(
        {"_id": obj_id},
        {"$push": {"discussion": message}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")
        
    return {"status": "success"}

@app.post("/admin/leave/requests/{request_id}/{action}")
async def handle_leave_request(request_id: str, action: str, admin: dict = Depends(get_current_admin)):
    """Handle (Approve/Reject/Cancel) a leave request (Role Scoped)."""
    try:
        obj_id = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID")

    status_map = {
        "approve": "approved",
        "reject": "rejected",
        "cancel": "cancelled"
    }
    if action not in status_map:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    # 1. Fetch Request
    leave_req = await leave_requests_collection.find_one({"_id": obj_id})
    if not leave_req:
        raise HTTPException(status_code=404, detail="Leave request not found")

    # 2. Check Permissions (Manager scoping)
    filter_query = get_employee_filter(admin)
    if "manager_id" in filter_query:
        # Check if the employee belongs to this manager
        emp = await employees_collection.find_one({"email": leave_req["employee_id"], "manager_id": admin.email})
        if not emp:
            raise HTTPException(status_code=403, detail="Access denied: You can only manage leaves for your direct reports.")
    elif admin.organization_id and admin.organization_id != leave_req.get("organization_id"):
        raise HTTPException(status_code=403, detail="Access denied: This request belongs to another organization.")

    # 3. Update Request
    result = await leave_requests_collection.update_one(
        {"_id": obj_id},
        {"$set": {
            "status": status_map[action],
            "processed_at": datetime.now(timezone.utc),
            "processed_by": admin.email
        }}
    )
        
    return {"status": "success"}

@app.get("/admin/export-logs-pdf")
async def export_logs_pdf(admin=Depends(get_current_admin)):
    """Stub for PDF export."""
    from fastapi.responses import Response
    return Response(content=b"PDF Content Stub", media_type="application/pdf")


@app.get("/admin/field/live-status")
async def get_field_live_status(admin=Depends(get_current_admin)):
    """Live operational data for the War Room map."""
    try:
        # Apply RBAC: managers see only their team
        base_filter = get_employee_filter(admin)
        query = {**base_filter, "employee_type": "field"}
            
        field_emps = await employees_collection.find(query).to_list(length=100)
        
        agents = []
        active_count = 0
        idle_count = 0
        breach_count = 0
        now = datetime.now(timezone.utc)
        
        for emp in field_emps:
            try:
                # 1. Get latest ping
                ping = await location_pings_collection.find_one(
                    {"employee_id": emp["email"]},
                    sort=[("recorded_at", -1)]
                )
                
                status = "Inactive"
                current_visit = None
                
                # 2. Check active check-in
                active_visit_log = await visit_logs_collection.find_one(
                    {"employee_id": emp["email"], "check_out": None},
                    sort=[("check_in", -1)]
                )
                
                if active_visit_log and active_visit_log.get("visit_id"):
                    plan = await visit_plans_collection.find_one({
                        "organization_id": emp["organization_id"],
                        "stops.visit_id": active_visit_log["visit_id"]
                    })
                    if plan:
                        current_stop = next((s for s in plan.get("stops", []) if s.get("visit_id") == active_visit_log["visit_id"]), None)
                        if current_stop:
                            current_visit = current_stop["place_name"]
        
                # 3. Status logic
                if ping and ping.get("recorded_at"):
                    rp = ping["recorded_at"]
                    if isinstance(rp, str):
                        try:
                            rp = datetime.fromisoformat(rp.replace("Z", "+00:00"))
                        except:
                            rp = now
                    
                    ping_time = rp.replace(tzinfo=timezone.utc) if rp.tzinfo is None else rp
                    if now - ping_time < timedelta(minutes=10):
                        status = "On-Site" if active_visit_log else "Traveling"
                        active_count += 1
                    else:
                        status = "Idle"
                        idle_count += 1
                
                # 4. KM today calculation
                start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                pings_today = await location_pings_collection.count_documents({
                    "employee_id": emp["email"],
                    "recorded_at": {"$gte": start_of_day}
                })
                
                # 5. Format response object
                lp = ping.get("recorded_at") if ping else None
                if isinstance(lp, datetime):
                    lp = lp.isoformat()
                    
                agents.append({
                    "id": str(emp["_id"]),
                    "email": str(emp["email"]),
                    "name": str(emp.get("full_name", emp["email"])),
                    "lat": float(ping.get("lat")) if ping and ping.get("lat") is not None else None,
                    "lng": float(ping.get("lng")) if ping and ping.get("lng") is not None else None,
                    "status": str(status),
                    "current_visit": str(current_visit) if current_visit else None,
                    "last_ping": str(lp) if lp else None,
                    "km_today": float(round(pings_today * 0.1, 1)),
                    "territory": emp.get("territory")
                })
            except Exception as e:
                logger.error(f"Inner loop error for {emp.get('email')}: {e}")
                continue
                
        res_data = {
            "agents": agents,
            "stats": {
                "active": int(active_count),
                "idle": int(idle_count),
                "breach": int(breach_count)
            }
        }
        return JSONResponse(content=res_data)
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        logger.error(f"TOP LEVEL LIVE STATUS ERROR: {err}")
        return JSONResponse(status_code=500, content={"error": str(e), "traceback": err})


@app.get("/admin/employees")
async def list_employees(admin=Depends(get_current_admin)):
    """List all employees scoped to the admin's organization."""
    query = {}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id
    employees = await employees_collection.find(query).to_list(length=1000)
    for emp in employees:
        emp["_id"] = str(emp["_id"])
        # Remove sensitive fields from response
        emp.pop("hashed_password", None)
        emp.pop("face_embedding", None)
    return employees


# -----------------------------------------------------------------------------
# EXPENSE CLAIM MANAGEMENT
# -----------------------------------------------------------------------------

@app.post("/api/field/expenses")
async def submit_expense(req: dict, employee=Depends(get_current_employee)):
    # Handle Base64 Receipt Image if present
    receipt_url = req.get("receipt_url", "")
    if receipt_url and receipt_url.startswith("data:image"):
        try:
            header, encoded = receipt_url.split(",", 1)
            ext = header.split(";")[0].split("/")[1]
            filename = f"receipt_{uuid.uuid4().hex}.{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(encoded))
            receipt_url = f"/uploads/{filename}"
        except Exception as e:
            logger.error(f"Failed to save receipt image: {e}")
            receipt_url = ""

    claim = {
        "employee_id": employee["email"],
        "organization_id": employee.get("organization_id"),
        "visit_id": req.get("visit_id"),
        "visit_plan_stop_id": req.get("visit_plan_stop_id"),  # v2: tag expense to a plan stop
        "expense_type": req.get("expense_type", "other"),
        "amount": float(req.get("amount", 0)),
        "description": req.get("description", ""),
        "receipt_url": receipt_url,
        "claimed_km": float(req.get("claimed_km")) if req.get("claimed_km") is not None else None,
        "auto_calculated_km": float(req.get("auto_calculated_km")) if req.get("auto_calculated_km") is not None else None,
        "nights": req.get("nights"),
        "accommodation_name": req.get("accommodation_name"),
        "location_city": req.get("location_city"),
        "status": "pending",
        "manager_query": None,
        "employee_response": None,
        "resolved_at": None,
        "created_at": datetime.now(timezone.utc)
    }
    result = await expense_claims_collection.insert_one(claim)
    return {"status": "success", "claim_id": str(result.inserted_id), "message": "Expense claim submitted"}


@app.get("/api/field/expenses")
async def get_my_expenses(employee=Depends(get_current_employee)):
    """Field employee fetches their own expense claims."""
    claims = await expense_claims_collection.find(
        {"employee_id": employee["email"]}
    ).sort("created_at", -1).to_list(length=100)
    for c in claims:
        c["_id"] = str(c["_id"])
        if isinstance(c.get("created_at"), datetime):
            c["created_at"] = c["created_at"].isoformat()
        if isinstance(c.get("resolved_at"), datetime):
            c["resolved_at"] = c["resolved_at"].isoformat()
    return claims


@app.get("/admin/expenses")
async def admin_list_expenses(status: Optional[str] = None, admin=Depends(get_current_admin)):
    """Admin fetches all expense claims, optionally filtered by status."""
    query = {}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id
    if status:
        query["status"] = status
    
    claims = await expense_claims_collection.find(query).sort("created_at", -1).to_list(length=200)
    for c in claims:
        c["_id"] = str(c["_id"])
        if isinstance(c.get("created_at"), datetime):
            c["created_at"] = c["created_at"].isoformat()
        if isinstance(c.get("resolved_at"), datetime):
            c["resolved_at"] = c["resolved_at"].isoformat()
        # Enrich with employee name
        emp = await employees_collection.find_one({"email": c.get("employee_id")})
        c["employee_name"] = emp["full_name"] if emp else c.get("employee_id", "Unknown")
    return claims


@app.put("/admin/expenses/{claim_id}")
async def admin_update_expense(claim_id: str, req: dict, admin=Depends(get_current_admin)):
    """Admin approves, rejects, or queries an expense claim."""
    from bson import ObjectId
    action = req.get("action", "approve")  # approve | reject | query
    
    query = {"_id": ObjectId(claim_id)}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id

    update_fields = {}
    if action == "approve":
        update_fields["status"] = "approved"
        update_fields["resolved_at"] = datetime.now(timezone.utc)
    elif action == "reject":
        update_fields["status"] = "rejected"
        update_fields["resolved_at"] = datetime.now(timezone.utc)
    elif action == "query":
        update_fields["status"] = "queried"
        update_fields["manager_query"] = req.get("query_text", "")
    
    result = await expense_claims_collection.update_one(
        query,
        {"$set": update_fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Expense claim not found or access denied")
    return {"status": "success", "message": f"Expense {action}d successfully"}


# -----------------------------------------------------------------------------
# ALERTS & FRAUD DETECTION
# -----------------------------------------------------------------------------

@app.get("/admin/field/alerts")
async def get_alerts(
    type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = "pending",
    employee_id: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Retrieve filtered security and operational alerts."""
    query = {"organization_id": admin.organization_id}
    if type: query["type"] = type
    if severity: query["severity"] = severity
    if status and status != "all": query["status"] = status
    if employee_id: query["employee_id"] = employee_id

    alerts = await alerts_collection.find(query).sort("timestamp", -1).to_list(length=100)
    
    for a in alerts:
        a["_id"] = str(a["_id"])
        # Enrich with employee name
        emp = await employees_collection.find_one({"email": a["employee_id"]})
        a["employee_name"] = emp["full_name"] if emp else a["employee_id"]
        if isinstance(a.get("timestamp"), datetime):
            a["timestamp"] = a["timestamp"].isoformat()
            
    return alerts

@app.put("/admin/field/alerts/{alert_id}")
async def update_alert_status(alert_id: str, req: dict, admin=Depends(get_current_admin)):
    """Update alert status (resolved, dismissed)."""
    from bson import ObjectId
    status = req.get("status")
    if status not in ["resolved", "dismissed", "pending"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    result = await alerts_collection.update_one(
        {"_id": ObjectId(alert_id), "organization_id": admin.organization_id},
        {"$set": {"status": status, "resolved_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "success", "message": f"Alert marked as {status}"}

# -----------------------------------------------------------------------------
# HEATMAP & SLA AUTOMATION
# -----------------------------------------------------------------------------

@app.get("/admin/field/heatmap-data")
async def get_heatmap_data(admin=Depends(get_current_admin)):
    """Aggregation of pings for heatmap visualization."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    base_filter = get_employee_filter(admin)
    query = {"recorded_at": {"$gte": start_of_day}}
    
    if "organization_id" in base_filter:
        query["organization_id"] = base_filter["organization_id"]
        
    if "manager_id" in base_filter:
        team = await employees_collection.find({"manager_id": base_filter["manager_id"]}).to_list(length=100)
        team_emails = [e["email"] for e in team]
        query["employee_id"] = {"$in": team_emails}
        
    pings = await location_pings_collection.find(query).to_list(length=5000)
    
    # Format for Leaflet.heat: [[lat, lng, intensity], ...]
    heatmap_data = [[p["lat"], p["lng"], 0.5] for p in pings]
    return heatmap_data


async def check_missed_visits():
    """Background check to flag missed visits for productivity alerts."""
    try:
        now = datetime.now(timezone.utc)
        # Using a simple check: if it's after 12:00 PM UTC (around 5:30 PM IST), run the check.
        if now.hour < 12:
            return
            
        today_str = now.strftime("%Y-%m-%d")
        # Find approved plans for today
        plans = await visit_plans_collection.find({"date": today_str, "status": "approved"}).to_list(length=1000)
        
        for plan in plans:
            pending_stops = [s for s in plan.get("stops", []) if s.get("status", "pending") == "pending"]
            if pending_stops:
                # Trigger alert for each agent with pending visits
                detail = f"SLA Breach: {len(pending_stops)} visits missed for today."
                
                # Deduplication: check if we already alerted today
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                existing = await alerts_collection.find_one({
                    "employee_id": plan["employee_id"],
                    "type": "Productivity",
                    "detail": {"$regex": "SLA Breach"},
                    "timestamp": {"$gte": day_start}
                })
                
                if not existing:
                    await trigger_alert(
                        "Productivity",
                        plan["employee_id"],
                        plan["organization_id"],
                        detail,
                        "medium"
                    )
    except Exception as e:
        logger.error(f"SLA Check failed: {e}")


@app.post("/admin/field/trigger-sla-check")
async def trigger_sla_check_manual(admin=Depends(get_current_admin)):
    """Manually trigger the SLA check for debugging."""
    await check_missed_visits()
    return {"status": "success", "message": "Manual SLA check triggered"}


# -----------------------------------------------------------------------------
# REPORTS & ANALYTICS
# -----------------------------------------------------------------------------

@app.get("/admin/reports/attendance")
async def attendance_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    employee_type: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Generate attendance report with filters."""
    query = {}
    
    scoped_ids = await get_scoped_employee_ids(admin)
    query["user_id"] = {"$in": scoped_ids}
    
    if start_date:
        query["timestamp"] = {"$gte": datetime.fromisoformat(start_date)}
    if end_date:
        if "timestamp" in query:
            query["timestamp"]["$lte"] = datetime.fromisoformat(end_date)
        else:
            query["timestamp"] = {"$lte": datetime.fromisoformat(end_date)}
    
    logs = await attendance_logs_collection.find(query).sort("timestamp", -1).to_list(length=1000)
    
    summary = {"total_records": len(logs), "check_ins": 0, "check_outs": 0, "unique_employees": set()}
    enriched = []
    
    for log in logs:
        emp = await employees_collection.find_one({"_id": ObjectId(log["user_id"])})
        if employee_type and emp and emp.get("employee_type", "desk") != employee_type:
            continue
        
        log["_id"] = str(log["_id"])
        log["full_name"] = emp["full_name"] if emp else "Unknown"
        log["employee_type"] = emp.get("employee_type", "desk") if emp else "unknown"
        if isinstance(log.get("timestamp"), datetime):
            log["timestamp"] = log["timestamp"].isoformat()
        
        if log.get("type") == "check-in":
            summary["check_ins"] += 1
        else:
            summary["check_outs"] += 1
        summary["unique_employees"].add(log.get("email", ""))
        enriched.append(log)
    
    summary["unique_employees"] = len(summary["unique_employees"])
    summary["total_records"] = len(enriched)
    
    return {"summary": summary, "records": enriched}


@app.get("/admin/reports/expenses")
async def expense_report(admin=Depends(get_current_admin)):
    """Generate expense summary report."""
    query = {}
    scoped_emails = await get_scoped_employee_emails(admin)
    query["employee_email"] = {"$in": scoped_emails}
    
    claims = await expense_claims_collection.find(query).to_list(length=1000)
    
    total_amount = sum(c.get("amount", 0) for c in claims)
    approved_amount = sum(c.get("amount", 0) for c in claims if c.get("status") == "approved")
    pending_amount = sum(c.get("amount", 0) for c in claims if c.get("status") == "pending")
    rejected_amount = sum(c.get("amount", 0) for c in claims if c.get("status") == "rejected")
    
    return {
        "total_claims": len(claims),
        "total_amount": total_amount,
        "approved_amount": approved_amount,
        "pending_amount": pending_amount,
        "rejected_amount": rejected_amount,
        "by_status": {
            "pending": len([c for c in claims if c.get("status") == "pending"]),
            "approved": len([c for c in claims if c.get("status") == "approved"]),
            "rejected": len([c for c in claims if c.get("status") == "rejected"]),
        }
    }


@app.get("/admin/reports/agent-performance")
async def agent_performance_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Aggregate visits and distance per agent."""
    org_id = admin.organization_id
    
    if not start_date or not end_date:
        now = datetime.now(timezone.utc)
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
    else:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    scoped_emp_ids = await get_scoped_employee_employee_ids(admin)

    pipeline = [
        {"$match": {
            "organization_id": org_id,
            "employee_id": {"$in": scoped_emp_ids},
            "check_in_time": {"$gte": start_dt, "$lte": end_dt}
        }},
        {"$group": {
            "_id": "$employee_id",
            "total_visits": {"$sum": 1},
            "leads": {"$sum": {"$cond": ["$lead_captured", 1, 0]}},
            "orders": {"$sum": {"$cond": ["$order_captured", 1, 0]}}
        }}
    ]
    visit_stats = await visit_logs_collection.aggregate(pipeline).to_list(None)

    km_pipeline = [
        {"$match": {
            "organization_id": org_id,
            "date": {"$gte": start_dt.strftime("%Y-%m-%d"), "$lte": end_dt.strftime("%Y-%m-%d")}
        }},
        {"$group": {
            "_id": "$employee_id",
            "total_km": {"$sum": "$total_km"}
        }}
    ]
    km_stats = await km_reimbursements_collection.aggregate(km_pipeline).to_list(None)

    perf_map = {}
    for stat in visit_stats:
        eid = stat["_id"]
        emp = await employees_collection.find_one({"employee_id": eid}, {"full_name": 1})
        perf_map[eid] = {
            "employee_id": eid,
            "full_name": emp["full_name"] if emp else "Unknown",
            "total_visits": stat["total_visits"],
            "leads": stat["leads"],
            "orders": stat["orders"],
            "total_km": 0
        }
    
    for kstat in km_stats:
        eid = kstat["_id"]
        if eid in perf_map:
            perf_map[eid]["total_km"] = round(kstat["total_km"], 2)
        else:
            emp = await employees_collection.find_one({"employee_id": eid}, {"full_name": 1})
            perf_map[eid] = {
                "employee_id": eid,
                "full_name": emp["full_name"] if emp else "Unknown",
                "total_visits": 0,
                "leads": 0,
                "orders": 0,
                "total_km": round(kstat["total_km"], 2)
            }

    return list(perf_map.values())
    

@app.get("/admin/reports/leaves")
async def leave_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Generate leave analytics report."""
    query = {}
    scoped_emails = await get_scoped_employee_emails(admin)
    query["employee_email"] = {"$in": scoped_emails}
    
    if start_date and end_date:
        query["start_date"] = {"$gte": start_date, "$lte": end_date}
    
    cursor = leave_requests_collection.find(query)
    leaves = await cursor.to_list(length=1000)
    
    # 1. Distribution by Type
    type_counts = {}
    for l in leaves:
        lt = l.get("leave_type", "other")
        type_counts[lt] = type_counts.get(lt, 0) + 1
    
    distribution = [{"name": k.replace("_", " ").capitalize(), "value": v} for k, v in type_counts.items()]
    
    # 2. Trends (Requests per day)
    trend_map = {}
    for l in leaves:
        cd = l.get("created_at")
        if cd:
            if isinstance(cd, datetime):
                ds = cd.strftime("%Y-%m-%d")
            else:
                ds = str(cd)[:10]
            trend_map[ds] = trend_map.get(ds, 0) + 1
            
    # Sort trends
    trends = [{"date": k, "count": v} for k, v in sorted(trend_map.items())]
    
    return {
        "distribution": distribution,
        "trends": trends,
        "total_requests": len(leaves),
        "approved": len([l for l in leaves if l.get("status") == "approved"]),
        "rejected": len([l for l in leaves if l.get("status") == "rejected"]),
        "pending": len([l for l in leaves if l.get("status") == "pending"])
    }


@app.get("/admin/reports/conversion-funnel")
async def conversion_funnel_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Aggregate visit outcomes for funnel visualization."""
    org_id = admin.organization_id
    
    if not start_date or not end_date:
        now = datetime.now(timezone.utc)
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
    else:
        start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    scoped_emp_ids = await get_scoped_employee_employee_ids(admin)

    pipeline = [
        {"$match": {
            "organization_id": org_id,
            "employee_id": {"$in": scoped_emp_ids},
            "check_in_time": {"$gte": start_dt, "$lte": end_dt}
        }},
        {"$group": {
            "_id": None,
            "visits": {"$sum": 1},
            "leads": {"$sum": {"$cond": ["$lead_captured", 1, 0]}},
            "orders": {"$sum": {"$cond": ["$order_captured", 1, 0]}}
        }}
    ]
    result = await visit_logs_collection.aggregate(pipeline).to_list(None)
    
    if not result:
        return {"visits": 0, "leads": 0, "orders": 0, "conversion_rate": 0}
    
    data = result[0]
    data.pop("_id", None)
    data["conversion_rate"] = round((data["orders"] / data["visits"] * 100), 2) if data["visits"] > 0 else 0
    return data


@app.get("/admin/reports/visit-frequency")
async def visit_frequency_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Daily trends analysis."""
    org_id = admin.organization_id
    
    if not start_date or not end_date:
        now = datetime.now(timezone.utc)
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
    else:
        start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

    scoped_emp_ids = await get_scoped_employee_employee_ids(admin)

    pipeline_daily = [
        {"$match": {
            "organization_id": org_id,
            "employee_id": {"$in": scoped_emp_ids},
            "check_in_time": {"$gte": start_dt, "$lte": end_dt}
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$check_in_time"}},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    daily_stats = await visit_logs_collection.aggregate(pipeline_daily).to_list(None)

    return {
        "daily_trends": [{"date": s["_id"], "visits": s["count"]} for s in daily_stats]
    }


# -----------------------------------------------------------------------------
# AREA 2: SMART VISIT PLAN TEMPLATES
# -----------------------------------------------------------------------------

@app.post("/api/field/plan/template")
async def create_plan_template(req: dict, employee=Depends(get_current_employee)):
    """Create a recurring visit plan template (e.g., Monday–Friday milk run)."""
    template_name = req.get("template_name")
    stops = req.get("stops", [])
    recurrence_days = req.get("recurrence_days", [0, 1, 2, 3, 4])  # Mon-Fri by default

    if not template_name:
        raise HTTPException(status_code=400, detail="template_name is required")
    if not stops or len(stops) == 0:
        raise HTTPException(status_code=400, detail="At least one stop is required")

    template = {
        "employee_id": employee["email"],
        "organization_id": employee.get("organization_id"),
        "template_name": template_name,
        "stops": stops,
        "recurrence_days": recurrence_days,  # 0=Mon, 1=Tue, ... 6=Sun
        "created_at": datetime.now(timezone.utc),
    }
    result = await visit_plan_templates_collection.insert_one(template)
    return {
        "status": "success",
        "template_id": str(result.inserted_id),
        "message": f"Template '{template_name}' saved with {len(stops)} stops."
    }


@app.get("/api/field/plan/templates/{employee_email}")
async def get_plan_templates(employee_email: str, employee=Depends(get_current_employee)):
    """List all recurring plan templates for an agent."""
    if employee["email"] != employee_email:
        raise HTTPException(status_code=403, detail="Cannot access another user's templates.")

    templates = await visit_plan_templates_collection.find(
        {"employee_id": employee_email}
    ).sort("created_at", -1).to_list(length=50)

    for t in templates:
        t["_id"] = str(t["_id"])
        if isinstance(t.get("created_at"), datetime):
            t["created_at"] = t["created_at"].isoformat()

    return templates


@app.delete("/api/field/plan/template/{template_id}")
async def delete_plan_template(template_id: str, employee=Depends(get_current_employee)):
    """Delete a recurring plan template."""
    from bson import ObjectId
    result = await visit_plan_templates_collection.delete_one({
        "_id": ObjectId(template_id),
        "employee_id": employee["email"]
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Template not found or not owned by you.")
    return {"status": "deleted"}


# -----------------------------------------------------------------------------
# AREA 3: LEADERBOARD
# -----------------------------------------------------------------------------

@app.get("/api/field/leaderboard")
async def get_field_leaderboard(employee=Depends(get_current_employee)):
    """
    Weekly leaderboard: Top 10 agents by visits completed + leads captured.
    Scoped to the employee's organization.
    """
    org_id = employee.get("organization_id")

    # Current week boundaries (Mon 00:00 → Sun 23:59)
    today = datetime.now(timezone.utc)
    week_start = today - timedelta(days=today.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    pipeline = [
        {
            "$match": {
                "organization_id": org_id,
                "check_in_time": {"$gte": week_start, "$lt": week_end}
            }
        },
        {
            "$group": {
                "_id": "$employee_id",
                "visits_completed": {"$sum": 1},
                "leads_captured": {"$sum": {"$cond": ["$lead_captured", 1, 0]}},
                "orders_captured": {"$sum": {"$cond": ["$order_captured", 1, 0]}},
            }
        },
        {"$sort": {"visits_completed": -1, "leads_captured": -1}},
        {"$limit": 10}
    ]

    results = await visit_logs_collection.aggregate(pipeline).to_list(length=10)

    leaderboard = []
    for idx, row in enumerate(results):
        emp = await employees_collection.find_one({"email": row["_id"]})
        emp_name = emp["full_name"] if emp else row["_id"]
        emp_designation = emp.get("designation", "Field Agent") if emp else "Field Agent"

        # Sum KM for the week from location pings
        pings = await location_pings_collection.find({
            "employee_id": row["_id"],
            "recorded_at": {"$gte": week_start, "$lt": week_end}
        }).sort("recorded_at", 1).to_list(length=5000)

        total_km = 0.0
        for i in range(1, len(pings)):
            p1, p2 = pings[i - 1], pings[i]
            dlat = math.radians(p2["lat"] - p1["lat"])
            dlng = math.radians(p2["lng"] - p1["lng"])
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(p1["lat"])) * math.cos(math.radians(p2["lat"])) * math.sin(dlng / 2) ** 2
            total_km += 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # Is this the requesting user?
        is_me = (row["_id"] == employee["email"])

        leaderboard.append({
            "rank": idx + 1,
            "employee_id": row["_id"],
            "name": emp_name,
            "designation": emp_designation,
            "visits_completed": row["visits_completed"],
            "leads_captured": row["leads_captured"],
            "orders_captured": row["orders_captured"],
            "distance_km": round(total_km, 1),
            "is_me": is_me,
        })

    # If requesting user is not in top 10, add their rank at the bottom
    if not any(e["is_me"] for e in leaderboard):
        my_stats = await visit_logs_collection.find({
            "employee_id": employee["email"],
            "organization_id": org_id,
            "check_in_time": {"$gte": week_start, "$lt": week_end}
        }).to_list(length=500)

        if my_stats:
            my_rank_pipeline = [
                {"$match": {"organization_id": org_id, "check_in_time": {"$gte": week_start, "$lt": week_end}}},
                {"$group": {"_id": "$employee_id", "visits_completed": {"$sum": 1}}},
                {"$sort": {"visits_completed": -1}}
            ]
            all_ranks = await visit_logs_collection.aggregate(my_rank_pipeline).to_list(length=1000)
            my_rank_pos = next((i + 1 for i, r in enumerate(all_ranks) if r["_id"] == employee["email"]), None)

            leaderboard.append({
                "rank": my_rank_pos or len(all_ranks) + 1,
                "employee_id": employee["email"],
                "name": employee.get("full_name", employee["email"]),
                "designation": employee.get("designation", "Field Agent"),
                "visits_completed": len(my_stats),
                "leads_captured": sum(1 for v in my_stats if v.get("lead_captured")),
                "orders_captured": sum(1 for v in my_stats if v.get("order_captured")),
                "distance_km": 0.0,
                "is_me": True,
            })

    return {
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": (week_end - timedelta(days=1)).strftime("%Y-%m-%d"),
        "leaderboard": leaderboard
    }


# -----------------------------------------------------------------------------
# AREA 3: MANAGER NUDGE
# -----------------------------------------------------------------------------

@app.post("/api/manager/nudge")
async def send_manager_nudge(req: dict, manager=Depends(get_current_employee)):
    """
    Manager sends a motivational nudge to one or more team members.
    Logged to DB. FCM push notification can be added here in production.
    """
    # Verify manager status
    subordinates_count = await employees_collection.count_documents({"manager_id": manager["email"]})
    if subordinates_count == 0:
        raise HTTPException(status_code=403, detail="Only managers can send nudges.")

    employee_emails = req.get("employee_emails", [])
    message = req.get("message", "").strip()
    nudge_type = req.get("nudge_type", "general")  # general | target_missed | late_start | great_job

    if not employee_emails:
        raise HTTPException(status_code=400, detail="At least one employee email is required.")
    if not message:
        raise HTTPException(status_code=400, detail="Nudge message cannot be empty.")

    # Verify all recipients belong to the manager
    valid_recipients = []
    for email in employee_emails:
        emp = await employees_collection.find_one({"email": email, "manager_id": manager["email"]})
        if emp:
            valid_recipients.append(email)

    if not valid_recipients:
        raise HTTPException(status_code=400, detail="No valid team members found in the provided emails.")

    # Save nudge log
    nudge_log = {
        "manager_id": manager["email"],
        "manager_name": manager.get("full_name", manager["email"]),
        "organization_id": manager.get("organization_id"),
        "recipients": valid_recipients,
        "message": message,
        "nudge_type": nudge_type,
        "sent_at": datetime.now(timezone.utc),
        # FCM push would go here in production: firebase_admin.messaging.send_multicast(...)
    }
    result = await nudge_logs_collection.insert_one(nudge_log)

    logger.info(f"Manager nudge sent by {manager['email']} to {valid_recipients}: [{nudge_type}] {message}")

    return {
        "status": "sent",
        "nudge_id": str(result.inserted_id),
        "recipients_count": len(valid_recipients),
        "recipients": valid_recipients,
        "message": f"Nudge sent to {len(valid_recipients)} team member(s)."
    }


@app.get("/api/manager/nudge/history")
async def get_nudge_history(manager=Depends(get_current_employee)):
    """Fetch the nudge history sent by this manager."""
    logs = await nudge_logs_collection.find(
        {"manager_id": manager["email"]}
    ).sort("sent_at", -1).to_list(length=50)

    for log in logs:
        log["_id"] = str(log["_id"])
        if isinstance(log.get("sent_at"), datetime):
            log["sent_at"] = log["sent_at"].isoformat()

    return logs

@app.post("/admin/nudge")
async def admin_send_nudge(req: dict, admin=Depends(get_current_admin)):
    """Admin sends a motivational nudge to field team members in their organization."""
    org_id = admin.organization_id
    employee_emails = req.get("employee_emails", [])
    message = req.get("message", "").strip()
    nudge_type = req.get("nudge_type", "general")

    if not employee_emails:
        raise HTTPException(status_code=400, detail="At least one employee email is required.")
    if not message:
        raise HTTPException(status_code=400, detail="Nudge message cannot be empty.")

    # Verify recipients belong to same organization
    valid_recipients = []
    for email in employee_emails:
        query = {"email": email}
        if org_id:
            query["organization_id"] = org_id
            
        emp = await employees_collection.find_one(query)
        if emp:
            valid_recipients.append(email)

    if not valid_recipients:
        raise HTTPException(status_code=400, detail="No valid employees found in your organization.")

    nudge_log = {
        "admin_id": admin.email,
        "admin_name": admin.full_name,
        "organization_id": org_id,
        "recipients": valid_recipients,
        "message": message,
        "nudge_type": nudge_type,
        "sent_at": datetime.now(timezone.utc),
    }
    result = await nudge_logs_collection.insert_one(nudge_log)
    return {
        "status": "sent",
        "nudge_id": str(result.inserted_id),
        "recipients_count": len(valid_recipients),
        "message": f"Nudge sent to {len(valid_recipients)} member(s)."
    }


@app.get("/admin/nudge/history")
async def admin_get_nudge_history(admin=Depends(get_current_admin)):
    """Fetch history of nudges sent by admins in this organization."""
    query = {}
    if admin.organization_id:
        query["organization_id"] = admin.organization_id
        
    logs = await nudge_logs_collection.find(query).sort("sent_at", -1).to_list(length=50)

    for log in logs:
        log["_id"] = str(log["_id"])
        if isinstance(log.get("sent_at"), datetime):
            log["sent_at"] = log["sent_at"].isoformat()

    return logs

@app.get("/admin/leaderboard")
async def get_admin_leaderboard(admin=Depends(get_current_admin)):
    """
    Weekly leaderboard for Admin Portal: Top 10 agents in the organization.
    """
    org_id = admin.organization_id

    # Current week boundaries (Mon 00:00 → Sun 23:59)
    today = datetime.now(timezone.utc)
    week_start = today - timedelta(days=today.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    pipeline = [
        {
            "$match": {
                "organization_id": org_id,
                "check_in_time": {"$gte": week_start, "$lt": week_end}
            }
        },
        {
            "$group": {
                "_id": "$employee_id",
                "visits_completed": {"$sum": 1},
                "leads_captured": {"$sum": {"$cond": ["$lead_captured", 1, 0]}},
                "orders_captured": {"$sum": {"$cond": ["$order_captured", 1, 0]}},
            }
        },
        {"$sort": {"visits_completed": -1, "leads_captured": -1}},
        {"$limit": 10}
    ]

    results = await visit_logs_collection.aggregate(pipeline).to_list(length=10)

    leaderboard = []
    for idx, row in enumerate(results):
        emp = await employees_collection.find_one({"email": row["_id"]})
        emp_name = emp["full_name"] if emp else row["_id"]
        emp_designation = emp.get("designation", "Field Agent") if emp else "Field Agent"

        # Sum KM for the week from location pings
        pings = await location_pings_collection.find({
            "employee_id": row["_id"],
            "recorded_at": {"$gte": week_start, "$lt": week_end}
        }).sort("recorded_at", 1).to_list(length=5000)

        total_km = 0.0
        for i in range(1, len(pings)):
            p1, p2 = pings[i - 1], pings[i]
            dlat = math.radians(p2["lat"] - p1["lat"])
            dlng = math.radians(p2["lng"] - p1["lng"])
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(p1["lat"])) * math.cos(math.radians(p2["lat"])) * math.sin(dlng / 2) ** 2
            total_km += 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        leaderboard.append({
            "rank": idx + 1,
            "employee_id": row["_id"],
            "name": emp_name,
            "designation": emp_designation,
            "visits_completed": row["visits_completed"],
            "leads_captured": row["leads_captured"],
            "orders_captured": row["orders_captured"],
            "distance_km": round(total_km, 1),
            "is_me": False, # Admins are not usually in the leaderboard
        })

    return {
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": (week_end - timedelta(days=1)).strftime("%Y-%m-%d"),
        "leaderboard": leaderboard
    }

# -----------------------------------------------------------------------------
# DATA SYNC & BULK MANAGEMENT
# -----------------------------------------------------------------------------

@app.get("/api/me/sync-status")
async def get_sync_status(last_sync: Optional[str] = None, employee=Depends(get_current_employee)):
    """Check for new data to sync to the mobile app."""
    # Simplified sync payload for pulling latest configurations
    profile = {
        "employee_id": employee.get("employee_id"),
        "full_name": employee.get("full_name"),
        "manager_id": employee.get("manager_id"),
        "territory": employee.get("territory"),
        "employee_type": employee.get("employee_type")
    }
    
    now = datetime.now(timezone.utc)
    
    return {
        "status": "success",
        "latest_profile": profile,
        "server_time": now.isoformat(),
        "requires_full_sync": True
    }


@app.get("/admin/reports/employee-monthly-summary")
async def get_employee_monthly_summary(
    email: str,
    month: str,  # Format: YYYY-MM
    admin=Depends(get_current_admin)
):
    """
    Get a detailed monthly summary for a specific employee.
    Used for individual drill-down reports.
    """
    try:
        start_date = datetime.strptime(f"{month}-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # End date is first day of next month
        year, m = map(int, month.split("-"))
        if m == 12:
            next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(year, m + 1, 1, tzinfo=timezone.utc)
            
        end_date = next_month
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid month format. Expected YYYY-MM")

    # Security: Ensure admin only sees their org's data
    org_filter = {"organization_id": admin.organization_id} if admin.organization_id else {}
    
    # 1. Fetch Employee Details
    employee = await employees_collection.find_one({"email": email, **org_filter})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # 2. Fetch All Logs for the month
    logs = await attendance_logs_collection.find({
        "email": email,
        "timestamp": {"$gte": start_date, "$lt": end_date}
    }).sort("timestamp", 1).to_list(length=1000)

    # 3. Fetch Approved Leaves for the month
    leaves = await leave_requests_collection.find({
        "sender_email": email,
        "status": "APPROVED",
        "$or": [
            {"start_date": {"$gte": start_date, "$lt": end_date}},
            {"end_date": {"$gte": start_date, "$lt": end_date}}
        ]
    }).to_list(length=50)

    # 4. Process Data Day by Day
    daily_breakdown = []
    total_working_ms = 0
    present_days = 0
    
    current_day = start_date
    while current_day < end_date:
        next_day = current_day + timedelta(days=1)
        day_str = current_day.strftime("%Y-%m-%d")
        
        # Filter logs for this day
        day_logs = [l for l in logs if current_day <= l["timestamp"].replace(tzinfo=timezone.utc) < next_day]
        
        day_info = {
            "date": day_str,
            "status": "Absent",
            "first_in": None,
            "last_out": None,
            "duration_hours": 0,
            "logs": []
        }

        # Check for Weekend (Saturday=5, Sunday=6)
        if current_day.weekday() >= 5:
            day_info["status"] = "Weekend"

        # Check for Leave
        for leave in leaves:
            l_start = leave["start_date"].replace(tzinfo=timezone.utc)
            l_end = leave["end_date"].replace(tzinfo=timezone.utc)
            if l_start <= current_day <= l_end:
                day_info["status"] = f"Leave ({leave.get('leave_type', 'General')})"
                break

        if day_logs:
            day_info["status"] = "Present"
            present_days += 1
            
            # Find Check-In / Check-Out pairs for duration
            day_logs.sort(key=lambda x: x["timestamp"])
            day_info["first_in"] = day_logs[0]["timestamp"].isoformat()
            
            # Calculate duration: simplify by taking last check-out - first check-in
            # Better: sum durations between paired IN and OUT
            day_duration_ms = 0
            last_in_time = None
            
            for log in day_logs:
                # Basic representation for frontend
                day_info["logs"].append({
                    "time": log["timestamp"].isoformat(),
                    "type": log["type"],
                    "method": log.get("check_in_method", "N/A"),
                    "location": log.get("location"),
                    "selfie": log.get("selfie_url"),
                    "wifi": log.get("wifi_details")
                })

                if log["type"] == "check-in":
                    last_in_time = log["timestamp"]
                elif log["type"] == "check-out" and last_in_time:
                    delta = log["timestamp"] - last_in_time
                    day_duration_ms += delta.total_seconds() * 1000
                    last_in_time = None
                    day_info["last_out"] = log["timestamp"].isoformat()

            day_info["duration_hours"] = round(day_duration_ms / (1000 * 3600), 2)
            total_working_ms += day_duration_ms

        daily_breakdown.append(day_info)
        current_day = next_day

    # 5. Summary Metrics
    total_hours = round(total_working_ms / (1000 * 3600), 2)
    avg_hours = round(total_hours / present_days, 2) if present_days > 0 else 0

    return {
        "employee": {
            "full_name": employee.get("full_name"),
            "email": employee.get("email"),
            "designation": employee.get("designation"),
            "department": employee.get("department"),
            "employee_type": employee.get("employee_type")
        },
        "summary": {
            "total_working_hours": total_hours,
            "average_daily_hours": avg_hours,
            "present_days": present_days,
            "leaves_taken": len(leaves)
        },
        "daily_breakdown": daily_breakdown
    }


@app.post("/admin/employees/bulk-update")
async def admin_bulk_update_employees(req: dict, admin=Depends(get_current_admin)):
    """Bulk update fields (like manager assignment or territory) for multiple employees."""
    employee_emails = req.get("employee_emails", [])
    updates = req.get("updates", {})
    
    if not employee_emails or not updates:
        raise HTTPException(status_code=400, detail="Missing emails or updates")
        
    # Security: Ensure admin only updates records they have access to
    base_filter = get_employee_filter(admin)
    query = {"email": {"$in": employee_emails}, **base_filter}
    
    # Restrict allowed update fields directly
    allowed_updates = ["manager_id", "territory", "employee_type"]
    filtered_updates = {k: v for k, v in updates.items() if k in allowed_updates}
    
    if not filtered_updates:
        raise HTTPException(status_code=400, detail="No valid update fields provided")
        
    result = await employees_collection.update_many(
        query,
        {"$set": filtered_updates}
    )
    
    return {
        "status": "success",
        "modified_count": result.modified_count,
        "matched_count": result.matched_count
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, reload_excludes=["*.log", "logs/*"])
