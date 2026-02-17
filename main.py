from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from datetime import datetime, timezone
import os
import math
from dotenv import load_dotenv

load_dotenv()

from database import employees_collection, attendance_logs_collection
from models import (
    RegisterRequest, LoginRequest, VerifyPresenceRequest, Token, LoginResponse, EmployeeProfile, UpdateFaceRequest
)
from auth import get_password_hash, verify_password, create_access_token
from face_utils import get_face_embedding, verify_face, compare_faces

app = FastAPI(title="OfficeFlow AI Attendance API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_db_client():
    """Create indexes on startup."""
    # Ensure email is unique and indexed
    await employees_collection.create_index("email", unique=True)
    # Ensure employee_id is indexed
    await employees_collection.create_index("employee_id")
    # Index attendance logs by user_id and timestamp for fast scans
    await attendance_logs_collection.create_index([("user_id", 1), ("timestamp", -1)])
    print("[Startup] MongoDB Indexes created successfully.")


@app.get("/")
async def root():
    return {"message": "OfficeFlow AI Attendance API is active", "status": "online"}


@app.get("/health")
async def health_check():
    """Health check endpoint to verify API and DB are connected."""
    try:
        from database import client
        await client.admin.command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    return {"api": "online", "database": db_status}


@app.post("/register", response_model=LoginResponse)
async def register(req: RegisterRequest):
    """Register a new employee with face image."""
    try:
        print(f"[Register] Received registration request for: {req.email}")
        print(f"[Register] Face image length: {len(req.face_image) if req.face_image else 'None'}")
        
        # Check if employee already exists
        existing = await employees_collection.find_one({"email": req.email})
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        # Generate face embedding
        print("[Register] Generating face embedding...")
        embedding = get_face_embedding(req.face_image)
        if embedding is None:
            raise HTTPException(status_code=400, detail="No face detected in image. Please try again with a clear photo.")
        
        print(f"[Register] Embedding generated successfully. Length: {len(embedding)}")

        # Create employee record
        hashed_password = get_password_hash(req.password)
        employee_dict = {
            "full_name": req.full_name,
            "email": req.email,
            "employee_id": req.employee_id,
            "designation": req.designation,
            "department": req.department,
            "hashed_password": hashed_password,
            "face_embedding": embedding,
            "device_id": req.device_id,
            "created_at": datetime.now(timezone.utc),
        }

        await employees_collection.insert_one(employee_dict)
        print(f"[Register] User {req.email} saved to database successfully.")

        # Generate token
        access_token = create_access_token(data={"sub": req.email})
        
        return {
            "access_token": access_token, 
            "token_type": "bearer",
            "user": {
                "full_name": employee_dict["full_name"],
                "email": employee_dict["email"],
                "employee_id": employee_dict["employee_id"],
                "designation": employee_dict["designation"],
                "department": employee_dict["department"],
                "created_at": employee_dict["created_at"]
            }
        }
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        import traceback
        pw_len = len(req.password) if req and hasattr(req, 'password') else 'unknown'
        error_msg = f"[Register] UNEXPECTED ERROR: {str(e)} (Password len: {pw_len})\n{traceback.format_exc()}"
        print(error_msg)
        try:
            with open("backend_errors.txt", "a") as f:
                f.write(f"\n--- Error at {datetime.utcnow()} ---\n")
                f.write(error_msg)
        except:
            pass
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)} (PW Len: {pw_len})")


@app.get("/analytics/{email}")
async def get_analytics(email: str):
    """Retrieve weekly/daily work hour stats for a user."""
    user = await employees_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Simple analytics: Aggregate duration_hours from logs in the last 7 days
    from datetime import timedelta
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    
    cursor = attendance_logs_collection.find({
        "user_id": str(user["_id"]),
        "type": "check-out",
        "timestamp": {"$gte": seven_days_ago}
    })
    logs = await cursor.to_list(length=100)
    
    daily_hours = {}
    total_week_hours = 0
    for log in logs:
        log_date = log["timestamp"]
        # Handle both datetime objects (new) and ISO strings (old)
        if isinstance(log_date, str):
            log_date = datetime.fromisoformat(log_date.replace("Z", "+00:00"))
            
        date_str = log_date.strftime("%Y-%m-%d")
        duration = log.get("duration_hours", 0)
        daily_hours[date_str] = daily_hours.get(date_str, 0) + duration
        total_week_hours += duration
    
    # Today's hours
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_hours = daily_hours.get(today_str, 0)
    
    # Determine current status (last log type)
    last_log = await attendance_logs_collection.find_one(
        {"user_id": str(user["_id"])},
        sort=[("timestamp", -1)]
    )
    current_status = last_log.get("type", "check-out") if last_log else "check-out"

    return {
        "today_hours": round(today_hours, 2),
        "week_total": round(total_week_hours, 2),
        "daily_breakdown": daily_hours,
        "current_status": current_status
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
            {"$set": {"face_embedding": embedding}}
        )
        
        return {"message": "Face data updated successfully. No attendance impact."}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[UpdateFace] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Face update failed: {str(e)}")
        raise
    except Exception as e:
        print(f"[UpdateFace] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Face update failed: {str(e)}")


@app.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Login with email and password."""
    user = await employees_collection.find_one({"email": req.email})
    if not user or not verify_password(req.password, user.get("hashed_password", "")):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    # Device Binding Check
    if user.get("device_id") and req.device_id and user["device_id"] != req.device_id:
        raise HTTPException(status_code=403, detail="Security Alert: Account locked to a different device. Please use your registered phone.")
    
    # Auto-bind on first login if not set
    if not user.get("device_id") and req.device_id:
        await employees_collection.update_one({"email": req.email}, {"$set": {"device_id": req.device_id}})

    access_token = create_access_token(data={"sub": req.email})
    
    # Return full profile with defaults for legacy accounts
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "user": {
            "full_name": user.get("full_name", "User"),
            "email": user.get("email", req.email),
            "employee_id": user.get("employee_id", "EMP-000"),
            "designation": user.get("designation", "Employee"),
            "department": user.get("department", "General"),
            "created_at": user.get("created_at", datetime.now(timezone.utc))
        }
    }


@app.get("/me", response_model=EmployeeProfile)
async def get_me(token: str):
    """Fetch current user profile from token hint (simplified for now)."""
    # In a full production app, this would use a 'get_current_user' dependency with JWT decoding.
    # For now, we'll keep it simple as requested for immediate office use.
    from auth import decode_access_token
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    email = payload.get("sub")
    user = await employees_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    return {
        "full_name": user.get("full_name", "Unknown"),
        "email": user.get("email", email),
        "employee_id": user.get("employee_id", "0000"),
        "designation": user.get("designation", "Employee"),
        "department": user.get("department", "General"),
        "created_at": user.get("created_at", datetime.now(timezone.utc))
    }


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
async def smart_attendance(req: VerifyPresenceRequest):
    """
    Unified endpoint for Smart Attendance (Check-in/Check-out):
    1. Validates WiFi signal strength (must be >= 80%)
    2. Extracts face embedding from image
    3. Searches all employees for a matching face (1:N)
    4. Auto-determines Check-in vs Check-out
    """
    try:
        # 1. WiFi Threshold Validation (User req: 80%)
        # Convert dBm to Percentage (Approx: -30dBm=100%, -90dBm=0%)
        # Formula: (strength + 90) * (100/60) -> slightly aggressive curve
        # Simplified: 2 * (strength + 100)
        wifi_pct = max(0, min(100, 2 * (req.wifi_strength + 100)))
        REQUIRED_WIFI_PCT = 80
        
        if wifi_pct < REQUIRED_WIFI_PCT:
            raise HTTPException(
                status_code=403, 
                detail=f"WiFi signal too weak ({wifi_pct:.0f}%). Required: {REQUIRED_WIFI_PCT}%. Get closer to the router."
            )

        # 2. Face Embedding
        new_embedding = get_face_embedding(req.image)
        if new_embedding is None:
            raise HTTPException(status_code=400, detail="No face detected in image.")

        # 3. 1:N Face Search
        # Fetch all employees
        employees = await employees_collection.find({}, {"_id": 1, "face_embedding": 1, "email": 1, "full_name": 1}).to_list(length=5000)
        
        matched_user = None
        
        # Priority: Check if the hint email matches first (to avoid 'Test' user collisions)
        hint_email = req.email # Passed from frontend as the logged-in user
        if hint_email and hint_email != "smart@auto.com":
            hint_user = await employees_collection.find_one({"email": hint_email})
            if hint_user and "face_embedding" in hint_user:
                if compare_faces(new_embedding, hint_user["face_embedding"]):
                    matched_user = hint_user

        if not matched_user:
            for emp in employees:
                if "face_embedding" in emp and emp["face_embedding"]:
                    if compare_faces(new_embedding, emp["face_embedding"]):
                        matched_user = emp
                        break
        
        if not matched_user:
            raise HTTPException(status_code=404, detail="Face not recognized. Please register first.")

        # 3.5 Device Binding Verification
        if matched_user.get("device_id") and req.device_id and matched_user["device_id"] != req.device_id:
             raise HTTPException(status_code=403, detail="Security violation: Hardware ID mismatch. Attendance must be marked from your registered device.")

        # 4. Geofencing Validation
        office_lat = float(os.getenv("OFFICE_LAT", 0))
        office_long = float(os.getenv("OFFICE_LONG", 0))
        radius = float(os.getenv("GEOFENCE_RADIUS_METERS", 4)) # Tightening to 4m as requested
        
        dlat = math.radians(req.lat - office_lat)
        dlon = math.radians(req.long - office_long)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(office_lat)) * math.cos(math.radians(req.lat)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        dist_meters = 6371000 * c
        
        if dist_meters > radius:
            raise HTTPException(
                status_code=403,
                detail=f"Location verification failed. You are {dist_meters:.0f}m away. Must be within {radius:.0f}m."
            )

        # 4.5 Mock Location Detection (Security)
        # Assuming frontend passes 'is_mocked' flag in metadata if available
        # Note: This requires the frontend to use a library that supports mock detection

        # 5. WiFi SSID/BSSID Validation
        target_ssid = os.getenv("OFFICE_WIFI_SSID", "")
        target_bssid = os.getenv("OFFICE_WIFI_BSSID", "")
        
        if target_ssid and req.wifi_ssid and req.wifi_ssid != target_ssid:
             raise HTTPException(status_code=403, detail=f"Must be connected to Office WiFi: {target_ssid}")
             
        if target_bssid and req.wifi_bssid and req.wifi_bssid.lower() != target_bssid.lower():
            raise HTTPException(status_code=403, detail="Connected to wrong WiFi access point (BSSID mismatch).")

        # 5. Determine Check-in/Check-out and Enforce Sequence
        last_log = await attendance_logs_collection.find_one(
            {"user_id": str(matched_user["_id"])},
            sort=[("timestamp", -1)]
        )
        
        last_type = last_log.get("type", "check-out") if last_log else "check-out"
        
        # Priority: Use user's intended type from frontend button
        if req.intended_type:
            attendance_type = req.intended_type
        else:
            attendance_type = "check-out" if last_type == "check-in" else "check-in"

        # Validation: Enforce clean sequence
        if attendance_type == last_type:
            status_text = "checked in" if last_type == "check-in" else "checked out"
            raise HTTPException(
                status_code=400, 
                detail=f"Attendance sequence error. You are already {status_text}. Please perform a {'check-out' if last_type == 'check-in' else 'check-in'} first."
            )

        session_info = {}
        if attendance_type == "check-out":
            # Try to find the matching check-in
            check_in_log = last_log if (last_log and last_log.get("type") == "check-in") else None
            # If the literal last log wasn't a check-in (maybe duplicated), search specifically
            if not check_in_log:
                 check_in_log = await attendance_logs_collection.find_one(
                    {"user_id": str(matched_user["_id"]), "type": "check-in"},
                    sort=[("timestamp", -1)]
                )
            
            if check_in_log:
                try:
                    # Parse timestamps
                    start_time = check_in_log["timestamp"]
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time)
                    
                    end_time = datetime.now(timezone.utc)
                    duration = end_time - start_time
                    hours = duration.total_seconds() / 3600
                    
                    session_info = {
                        "check_in_time": start_time.isoformat(),
                        "check_in_address": check_in_log.get("address", "Office"),
                        "duration_hours": round(hours, 2)
                    }
                except Exception as e:
                    print(f"Duration calculation failed: {e}")

        # 5. Save Log
        log_entry = {
            "user_id": str(matched_user["_id"]),
            "email": matched_user["email"],
            "full_name": matched_user["full_name"],
            "timestamp": datetime.now(timezone.utc),
            "type": attendance_type,
            "status": "success",
            "lat": req.lat,
            "long": req.long,
            "address": req.address,
            "distance_meters": dist_meters,
            "wifi_ssid": req.wifi_ssid,
            "wifi_quality": req.wifi_strength,
            **session_info 
        }
        await attendance_logs_collection.insert_one(log_entry)

        return {
            "status": "success",
            "type": attendance_type,
            "user": matched_user["full_name"],
            "message": f"{attendance_type.replace('-', ' ').title()} successful for {matched_user['full_name']}",
            "wifi_quality": f"{wifi_pct:.0f}%",
            "time": log_entry["timestamp"].isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[SmartAttendance] CRITICAL ERROR: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")


@app.get("/logs/{email}")
async def get_logs(email: str):
    """Get attendance logs for a user."""
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


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
