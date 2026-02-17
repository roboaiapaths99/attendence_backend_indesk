from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class EmployeeBase(BaseModel):
    full_name: str
    email: str
    employee_id: str
    designation: str = "Employee"
    department: str = "General"


class EmployeeCreate(EmployeeBase):
    password: str


class EmployeeProfile(EmployeeBase):
    created_at: datetime


class EmployeeDB(EmployeeBase):
    id: str = Field(alias="_id")
    face_embedding: List[float]
    device_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LocationData(BaseModel):
    lat: float
    long: float


class AttendanceLog(BaseModel):
    user_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: str  # "check-in" or "check-out"
    location: Optional[LocationData] = None
    wifi_confidence: float = 0.0
    confidence_score: float = 0.0


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: EmployeeProfile


# Request models for API endpoints
class RegisterRequest(BaseModel):
    full_name: str
    email: str
    employee_id: str
    designation: str
    department: str
    password: str
    face_image: str  # base64 encoded image
    device_id: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str
    device_id: Optional[str] = None


class VerifyPresenceRequest(BaseModel):
    email: str
    image: str  # base64 encoded image
    lat: float
    long: float
    wifi_ssid: str = ""
    wifi_bssid: str = ""
    wifi_strength: float = -50.0
    address: Optional[str] = None
    intended_type: Optional[str] = None  # "check-in" or "check-out"
    device_id: Optional[str] = None
class UpdateFaceRequest(BaseModel):
    email: str
    password: str
    face_image: str  # base64 encoded image
    lat: float
    long: float
    wifi_ssid: str = ""
    wifi_bssid: str = ""
    wifi_strength: float = -50.0
    device_id: Optional[str] = None
