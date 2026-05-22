from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


SubscriptionPlan = Literal["free", "pro", "ultra", "enterprise"]


class AppMembership(BaseModel):
    app_id: str
    roles: List[str] = Field(default_factory=lambda: ["user"])
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = None


class UserInDB(BaseModel):
    email: EmailStr
    pharmacy_id: Optional[str] = None
    hashed_password: Optional[str] = None
    google_id: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    is_active: bool = True
    global_role: str = "user"
    subscription_plan: SubscriptionPlan = "free"
    allowed_apps: List[str] = Field(default_factory=list)
    memberships: List[AppMembership] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    
    # Pharmacy Details
    pharmacy_name: Optional[str] = None
    owner_name: Optional[str] = None
    license_number: Optional[str] = None
    store_type: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    age: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    whatsapp: Optional[str] = None
    telegram: Optional[str] = None

    model_config = {"extra": "allow"}


class AppAwareRequest(BaseModel):
    app_id: str = Field(..., min_length=2, max_length=50)
    requested_role: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=50,
        description="Role requested for the app, for example customer, medical_owner, delivery_partner.",
    )

    @field_validator("app_id", "requested_role")
    @classmethod
    def normalize_values(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip().lower().replace(" ", "_")


class SignupRequest(AppAwareRequest):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")
    name: Optional[str] = None


class LoginRequest(AppAwareRequest):
    email: EmailStr
    password: str


class GoogleAuthRequest(AppAwareRequest):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserResponse"


class UserResponse(BaseModel):
    email: EmailStr
    pharmacy_id: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    global_role: str
    subscription_plan: SubscriptionPlan
    allowed_apps: List[str]
    memberships: List[AppMembership]
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    
    # Pharmacy Details
    pharmacy_name: Optional[str] = None
    owner_name: Optional[str] = None
    license_number: Optional[str] = None
    store_type: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    age: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    whatsapp: Optional[str] = None
    telegram: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    pharmacy_name: Optional[str] = None
    owner_name: Optional[str] = None
    license_number: Optional[str] = None
    store_type: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None

class CompleteProfileRequest(BaseModel):
    name: str = Field(..., min_length=2)
    age: str = Field(..., min_length=1)
    address: str = Field(..., min_length=5)
    role: str = Field(..., min_length=2)


class SupportedApp(BaseModel):
    app_id: str
    description: str


class SupportedAppsResponse(BaseModel):
    apps: List[SupportedApp]
