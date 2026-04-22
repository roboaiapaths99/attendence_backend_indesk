from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
import os
from dotenv import load_dotenv

load_dotenv()

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()

# JWT configuration
SECRET_KEY = os.getenv("JWT_SECRET", "supersecretkey")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
# Handle trailing comments in Docker --env-file
expire_minutes_str = str(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200")).split('#')[0].strip()
ACCESS_TOKEN_EXPIRE_MINUTES = int(expire_minutes_str)

def verify_password(plain_password, hashed_password):
    try:
        return ph.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Fallback for old pbkdf2 hashes during transition if needed
        # But for now, we'll assume Argon2 or fail
        return False

def get_password_hash(password):
    return ph.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from database import admins_collection, employees_collection
from models import Admin

admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/login")
employee_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

async def get_current_admin(token: str = Depends(admin_oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub").lower().strip()
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    admin = await admins_collection.find_one({"email": email})
    if admin is None:
        # Check hardcoded superadmin fallback from .env
        fallback_email = os.getenv("ADMIN_EMAIL", "admin@officeflow.ai")
        if email == fallback_email:
             return Admin(
                 email=email, 
                 role="superadmin", 
                 full_name="System Super Admin",
                 organization_id="system_org",
                 allowed_features=["dashboard", "employees", "attendance", "leaves", "expenses", "reports", "war_room", "territory", "nudge", "leaderboard", "settings", "admins"]
             )
        raise credentials_exception
    
    # Ensure role exists, default to 'hr' if missing for some reason
    if "role" not in admin:
        admin["role"] = "hr"
    
    # Ensure allowed_features exists
    if "allowed_features" not in admin:
        admin["allowed_features"] = ["dashboard"]
        
    return Admin(**admin)


async def get_current_employee(token: str = Depends(employee_oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub").lower().strip()
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    employee = await employees_collection.find_one({"email": email})
    if employee is None:
        raise credentials_exception
        
    return employee
