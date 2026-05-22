from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pymongo import ReturnDocument
from starlette.requests import Request
from starlette.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow

import os
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db.mongo import get_db
from app.models.user import (
    AppMembership,
    CompleteProfileRequest,
    GoogleAuthRequest,
    LoginRequest,
    ProfileUpdateRequest,
    SignupRequest,
    SupportedApp,
    SupportedAppsResponse,
    TokenResponse,
    UserResponse,
)
from pydantic import BaseModel, EmailStr

class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "staff"

router = APIRouter(prefix="/auth", tags=["Authentication"])

APP_DESCRIPTIONS = {
    "dashboard": "Central dashboard where subscription and master user profile are managed.",
    "storefront": "Business-facing app that mirrors dashboard access for medicine sellers and operators.",
    "ops_hub": "Operations app where customers, medical stores, and delivery partners can sign in.",
}

def _is_localhost_url(url: str) -> bool:
    lower = url.lower()
    return "localhost" in lower or "127.0.0.1" in lower


def _normalize_frontend_base(url: str | None) -> str | None:
    if not url:
        return None
    normalized = url.strip().rstrip("/")
    if not normalized:
        return None
    if settings.APP_ENV == "production" and _is_localhost_url(normalized):
        return None
    return normalized


def _resolve_frontend_base(app_id: str) -> str:
    app_specific_map = {
        "dashboard": settings.FRONTEND_DASHBOARD,
        "storefront": settings.FRONTEND_STOREFRONT,
        "ops_hub": settings.FRONTEND_OPS_HUB,
    }

    app_specific = _normalize_frontend_base(app_specific_map.get(app_id))
    if app_specific:
        return app_specific

    fallback = _normalize_frontend_base(settings.FRONTEND_URL)
    if fallback:
        return fallback

    return "http://localhost:5173"


def _ensure_supported_app(app_id: str) -> None:
    if app_id not in settings.supported_apps_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported app_id '{app_id}'. Allowed apps: {', '.join(settings.supported_apps_list)}",
        )


def _sanitize_memberships(raw_memberships: list[dict[str, Any]] | None) -> list[AppMembership]:
    memberships = raw_memberships or []
    return [AppMembership(**membership) for membership in memberships]


def _build_user_response(user: dict[str, Any]) -> UserResponse:
    return UserResponse(
        email=user["email"],
        pharmacy_id=user.get("pharmacy_id"),
        name=user.get("name"),
        picture=user.get("picture"),
        global_role=user.get("global_role", "user"),
        subscription_plan=user.get("subscription_plan", settings.DEFAULT_SUBSCRIPTION_PLAN),
        allowed_apps=user.get("allowed_apps", []),
        memberships=_sanitize_memberships(user.get("memberships")),
        is_active=user.get("is_active", True),
        created_at=user["created_at"],
        last_login=user.get("last_login"),
        # Pharmacy Details
        pharmacy_name=user.get("pharmacy_name"),
        owner_name=user.get("owner_name"),
        license_number=user.get("license_number"),
        store_type=user.get("store_type"),
        phone_number=user.get("phone_number"),
        address=user.get("address"),
        age=user.get("age"),
        lat=user.get("lat"),
        lng=user.get("lng"),
        whatsapp=user.get("whatsapp"),
        telegram=user.get("telegram"),
    )


def _build_token_payload(user: dict[str, Any], app_id: str, active_role: str) -> dict[str, Any]:
    pharmacy_id = user.get("pharmacy_id")
    return {
        "sub": user["email"],
        "email": user["email"],
        "pharmacy_id": pharmacy_id,
        "merchant_id": pharmacy_id,
        "app_id": app_id,
        "active_role": active_role,
        "global_role": user.get("global_role", "user"),
        "subscription_plan": user.get("subscription_plan", settings.DEFAULT_SUBSCRIPTION_PLAN),
        "allowed_apps": user.get("allowed_apps", []),
    }


async def _ensure_pharmacy_identity(db, user: dict[str, Any]) -> dict[str, Any]:
    if user.get("pharmacy_id"):
        return user

    # 1. Check if this user was invited to join an existing pharmacy
    invitation = await db["invitations"].find_one({
        "email": user.get("email"), 
        "status": "pending"
    })

    if invitation:
        pharmacy_id = invitation["pharmacy_id"]
        # Mark invitation as accepted
        await db["invitations"].update_one(
            {"_id": invitation["_id"]}, 
            {"$set": {"status": "accepted", "accepted_at": datetime.utcnow()}}
        )
    else:
        # 2. No invitation? Generate a new Workspace/Pharmacy ID for this owner
        raw_id = user.get("_id")
        if raw_id is None:
            return user
        pharmacy_id = f"PHARM_{str(raw_id)}"

    # Update the user profile with their new or inherited pharmacy_id
    updated = await db["users"].find_one_and_update(
        {"email": user["email"]},
        {"$set": {"pharmacy_id": pharmacy_id, "updated_at": datetime.utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    return updated or {**user, "pharmacy_id": pharmacy_id}


def _resolve_active_role(
    user: dict[str, Any],
    app_id: str,
    requested_role: str | None,
) -> str:
    if requested_role:
        return requested_role

    memberships = user.get("memberships", [])
    for membership in memberships:
        if membership.get("app_id") == app_id:
            roles = membership.get("roles") or []
            if roles:
                return roles[0]
    return "user"


async def _touch_membership(
    db,
    *,
    user_email: str,
    app_id: str,
    requested_role: str | None,
) -> dict[str, Any]:
    now = datetime.utcnow()
    user = await db["users"].find_one({"email": user_email})
    if not user:
        return None

    allowed_apps = list(user.get("allowed_apps", []) or [])
    if app_id not in allowed_apps:
        allowed_apps.append(app_id)

    memberships = list(user.get("memberships", []) or [])
    target_membership = None
    for membership in memberships:
        if membership.get("app_id") == app_id:
            target_membership = membership
            break

    if target_membership is None:
        target_membership = {
            "app_id": app_id,
            "roles": [requested_role or "user"],
            "joined_at": now,
            "last_login_at": now,
        }
        memberships.append(target_membership)
    else:
        roles = list(target_membership.get("roles", []) or [])
        next_role = requested_role or (roles[0] if roles else "user")
        if next_role not in roles:
            roles.append(next_role)
        target_membership["roles"] = roles
        target_membership["last_login_at"] = now

    await db["users"].update_one(
        {"email": user_email},
        {
            "$set": {
                "allowed_apps": allowed_apps,
                "memberships": memberships,
                "updated_at": now,
                "last_login": now,
            }
        },
    )
    return await db["users"].find_one({"email": user_email})


async def _issue_token_response(
    db,
    *,
    user_email: str,
    app_id: str,
    requested_role: str | None,
) -> TokenResponse:
    user = await _touch_membership(
        db,
        user_email=user_email,
        app_id=app_id,
        requested_role=requested_role,
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to build login session.",
        )

    user = await _ensure_pharmacy_identity(db, user)
    active_role = _resolve_active_role(user, app_id, requested_role)
    token, expires_in = create_access_token(_build_token_payload(user, app_id, active_role))
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=_build_user_response(user),
    )


@router.post("/signup", summary="Register a new user", response_model=TokenResponse)
async def signup(signup_data: SignupRequest):
    _ensure_supported_app(signup_data.app_id)
    db = get_db()
    
    # Check if user already exists
    existing_user = await db["users"].find_one({"email": signup_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists.",
        )
    
    now = datetime.utcnow()
    user_in_db = {
        "email": signup_data.email,
        "hashed_password": hash_password(signup_data.password),
        "name": signup_data.name,
        "is_active": True,
        "global_role": "user",
        "subscription_plan": settings.DEFAULT_SUBSCRIPTION_PLAN,
        "created_at": now,
        "updated_at": now,
        "last_login": now,
        "allowed_apps": [signup_data.app_id],
        "memberships": [
            {
                "app_id": signup_data.app_id,
                "roles": [signup_data.requested_role or "user"],
                "joined_at": now,
                "last_login_at": now,
            }
        ],
    }
    
    await db["users"].insert_one(user_in_db)
    
    return await _issue_token_response(
        db,
        user_email=signup_data.email,
        app_id=signup_data.app_id,
        requested_role=signup_data.requested_role,
    )


@router.post("/login", summary="Login with email/password", response_model=TokenResponse)
async def login(login_data: LoginRequest):
    _ensure_supported_app(login_data.app_id)
    db = get_db()
    
    user = await db["users"].find_one({"email": login_data.email})
    if not user or not user.get("hashed_password") or not verify_password(login_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is deactivated.",
        )
    
    return await _issue_token_response(
        db,
        user_email=login_data.email,
        app_id=login_data.app_id,
        requested_role=login_data.requested_role,
    )


@router.post("/google/token", summary="Verify Google ID Token", response_model=TokenResponse)
async def google_auth_token(auth_data: GoogleAuthRequest):
    """
    Verifies a Google ID Token sent from a mobile app.
    """
    _ensure_supported_app(auth_data.app_id)
    
    try:
        id_info = google_id_token.verify_oauth2_token(
            auth_data.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=300,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google token: {exc}",
        )

    email = id_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email field in Google token.",
        )

    db = get_db()
    now = datetime.utcnow()
    
    user = await db["users"].find_one_and_update(
        {"email": email},
        {
            "$set": {
                "google_id": id_info["sub"],
                "name": id_info.get("name"),
                "picture": id_info.get("picture"),
                "updated_at": now,
                "last_login": now,
            },
            "$setOnInsert": {
                "created_at": now,
                "is_active": True,
                "global_role": "user",
                "subscription_plan": settings.DEFAULT_SUBSCRIPTION_PLAN,
                "allowed_apps": [],
                "memberships": [],
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )

    return await _issue_token_response(
        db,
        user_email=email,
        app_id=auth_data.app_id,
        requested_role=auth_data.requested_role,
    )


@router.get(
    "/apps",
    summary="List supported client applications",
    response_model=SupportedAppsResponse,
)
async def list_supported_apps():
    apps = [
        SupportedApp(app_id=app_id, description=APP_DESCRIPTIONS.get(app_id, "Configured client application."))
        for app_id in settings.supported_apps_list
    ]
    return SupportedAppsResponse(apps=apps)


@router.get("/pharmacies", summary="List all registered pharmacies", response_model=list[UserResponse])
async def list_pharmacies():
    """
    Returns all users who have an active pharmacy_id.
    This serves as the 'actual database' list for the map.
    """
    db = get_db()
    cursor = db["users"].find({
        "pharmacy_id": {"$exists": True, "$ne": None},
        "is_active": True
    })
    pharmacies = await cursor.to_list(length=100)
    return [_build_user_response(p) for p in pharmacies]


@router.get("/google/login", summary="Initiate Google OAuth Flow")
async def google_login(
    request: Request,
    app_id: str = "dashboard",
    requested_role: str = "user",
):
    """
    Redirects the user to Google's OAuth2 authorization page.
    This is for testing server-side flow in the browser.
    """
    _ensure_supported_app(app_id)

    # Use PUBLIC_URL from settings if provided, otherwise fallback to request.base_url
    base = settings.PUBLIC_URL or str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/google/callback"

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
    )
    flow.redirect_uri = redirect_uri

    # Encode app_id and role into state itself so callback can recover them
    # even when session cookie is lost (e.g. across ngrok tunnels).
    # Format: "<google_state>|<app_id>|<role>"
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=f"placeholder|{app_id}|{requested_role}",  # will be overridden below
    )

    # Extract the actual state Google assigned (it may override ours), then rebuild
    from urllib.parse import urlparse, parse_qs, urlencode as ue, urlunparse
    parsed_auth = urlparse(authorization_url)
    qp = parse_qs(parsed_auth.query)
    raw_state = qp.get("state", [state])[0]
    parts = raw_state.split("|")
    if len(parts) >= 3 and parts[-2] == app_id and parts[-1] == requested_role:
        google_state = "|".join(parts[:-2]) or parts[0]
    else:
        google_state = raw_state
    encoded_state = f"{google_state}|{app_id}|{requested_role}"
    # Patch state in the authorization_url
    new_qp = {k: v[0] for k, v in qp.items()}
    new_qp["state"] = encoded_state
    authorization_url = urlunparse((
        parsed_auth.scheme, parsed_auth.netloc, parsed_auth.path,
        parsed_auth.params, ue(new_qp), parsed_auth.fragment,
    ))

    # Also store in session as primary mechanism
    request.session["oauth_state"] = encoded_state
    request.session["oauth_app_id"] = app_id
    request.session["oauth_requested_role"] = requested_role

    return RedirectResponse(authorization_url)


@router.get("/google/callback", summary="Google OAuth Callback")
async def google_callback(request: Request):
    """
    Handles the callback from Google. Exchanges the code for tokens and issues a local JWT.
    Works correctly behind ngrok/reverse-proxies by forcing https:// in the
    authorization_response URL using PUBLIC_URL.
    """
    # ── 1. Recover state & app context ───────────────────────────────────────
    # State is encoded as "<google_state>|<app_id>|<role>" in google_login.
    incoming_state = request.query_params.get("state", "")

    # Try session first (most reliable), then fall back to the encoded state in URL
    state_in_session = request.session.get("oauth_state")
    app_id = request.session.get("oauth_app_id", "dashboard")
    requested_role = request.session.get("oauth_requested_role", "user")

    if state_in_session:
        # Session is alive — parse app_id/role from the encoded session state
        parts = state_in_session.split("|")
        if len(parts) >= 3:
            app_id = parts[-2] or app_id
            requested_role = parts[-1] or requested_role
        # The full encoded state is what was registered with Google
        flow_state = state_in_session
    elif incoming_state:
        # Session cookie was lost (ngrok domain mismatch) — decode from URL state
        parts = incoming_state.split("|")
        if len(parts) >= 3:
            flow_state = incoming_state   # full encoded state matches what Google got
            app_id = parts[-2] or app_id
            requested_role = parts[-1] or requested_role
        else:
            flow_state = incoming_state   # plain state, no context encoded
    else:
        raise HTTPException(status_code=400, detail="OAuth state not found in session or URL.")

    # ── 2. Build redirect_uri (must match what was registered with Google) ────
    base = settings.PUBLIC_URL.rstrip("/") if settings.PUBLIC_URL else str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/google/callback"

    # ── 3. Reconstruct authorization_response with correct https:// scheme ───
    # ngrok forwards requests internally as http:// even though the outer URL is
    # https://. Google's redirect_uri check is scheme-sensitive, so we must
    # rebuild the URL using the PUBLIC_URL base.
    raw_url = str(request.url)  # e.g. http://127.0.0.1:8000/auth/google/callback?code=...&state=...
    # Replace the scheme+host with the public base
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(raw_url)
    public_parsed = urlparse(base)
    fixed_url = urlunparse((
        public_parsed.scheme,
        public_parsed.netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))

    # ── 4. Build OAuth flow ───────────────────────────────────────────────────
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
        state=flow_state,
    )
    flow.redirect_uri = redirect_uri

    # ── 5. Exchange code for tokens ───────────────────────────────────────────
    try:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # allow http during local dev
        flow.fetch_token(authorization_response=fixed_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch token: {exc}")

    # ── 6. Verify ID token ────────────────────────────────────────────────────
    credentials = flow.credentials
    try:
        id_info = google_id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=300,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Google token verification failed: {exc}")

    email = id_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email from Google.")

    # ── 7. Upsert user in MongoDB ─────────────────────────────────────────────
    db = get_db()
    now = datetime.utcnow()
    try:
        user = await db["users"].find_one_and_update(
            {"email": email},
            {
                "$set": {
                    "google_id": id_info["sub"],
                    "name": id_info.get("name"),
                    "picture": id_info.get("picture"),
                    "updated_at": now,
                    "last_login": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                    "is_active": True,
                    "global_role": "user",
                    "subscription_plan": settings.DEFAULT_SUBSCRIPTION_PLAN,
                    "allowed_apps": [],
                    "memberships": [],
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Mongo write failed during Google login: {exc}")

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account is deactivated.")

    # ── 8. Clear session ──────────────────────────────────────────────────────
    request.session.pop("oauth_state", None)
    request.session.pop("oauth_app_id", None)
    request.session.pop("oauth_requested_role", None)

    # ── 9. Issue JWT & redirect to frontend ───────────────────────────────────
    try:
        token_resp = await _issue_token_response(
            db,
            user_email=email,
            app_id=app_id,
            requested_role=requested_role,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Session token creation failed: {exc}")

    frontend_base = _resolve_frontend_base(app_id)
    query = urlencode({"token": token_resp.access_token})
    redirect_url = f"{frontend_base}/?{query}"
    return RedirectResponse(url=redirect_url)


@router.get("/me", summary="Get current user profile", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user = await db["users"].find_one({"email": current_user["email"]})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return _build_user_response(user)


@router.get("/me/context", summary="Get current user profile with active app context")
async def get_me_context(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user = await db["users"].find_one({"email": current_user["email"]})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    app_id = current_user.get("app_id")
    memberships = user.get("memberships", []) or []
    active_membership = None
    for membership in memberships:
        if membership.get("app_id") == app_id:
            active_membership = membership
            break

    return {
        "user": _build_user_response(user),
        "active_app_id": app_id,
        "active_role": current_user.get("active_role"),
        "membership": active_membership,
    }


@router.put("/profile", summary="Update current user profile", response_model=UserResponse)
async def update_profile(
    profile_data: ProfileUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    
    # Remove None values from update data
    update_dict = {k: v for k, v in profile_data.model_dump().items() if v is not None}
    
    if not update_dict:
        # If nothing to update, just return current user
        user = await db["users"].find_one({"email": current_user["email"]})
        return _build_user_response(user)
    
    update_dict["updated_at"] = datetime.utcnow()
    
    user = await db["users"].find_one_and_update(
        {"email": current_user["email"]},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
        
    return _build_user_response(user)


@router.post("/complete-profile", summary="Complete social auth profile registration", response_model=UserResponse)
async def complete_profile(
    profile_data: CompleteProfileRequest,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    now = datetime.utcnow()
    
    # 1. Update the primary users collection
    user_update = {
        "name": profile_data.name,
        "age": profile_data.age,
        "address": profile_data.address,
        "updated_at": now,
    }
    
    user = await db["users"].find_one_and_update(
        {"email": current_user["email"]},
        {"$set": user_update},
        return_document=ReturnDocument.AFTER
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
        
    # 2. Add to role-specific collections (Patients vs Delivery Partners)
    role = profile_data.role.lower()
    profile_doc = {
        "email": current_user["email"],
        "name": profile_data.name,
        "age": profile_data.age,
        "address": profile_data.address,
        "updated_at": now,
        "profile_completed": True,
    }

    if "delivery" in role:
        # Save to 'delivery_partners' collection
        await db["delivery_partners"].update_one(
            {"email": current_user["email"]},
            {"$set": profile_doc, "$setOnInsert": {"created_at": now}},
            upsert=True
        )
    else:
        # Default to 'patients' for customers/patients
        await db["patients"].update_one(
            {"email": current_user["email"]},
            {"$set": profile_doc, "$setOnInsert": {"created_at": now}},
            upsert=True
        )
        
    return _build_user_response(user)


@router.post("/logout", summary="Logout current user")
async def logout(current_user: dict = Depends(get_current_user)):
    return {
        "status": "success",
        "message": f"Session closed for {current_user['email']}. Delete the token on the client.",
    }


@router.post("/invite", summary="Invite staff to your pharmacy workspace")
async def invite_staff(
    invite_data: InviteRequest,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    
    # 1. Verify the current user has a pharmacy_id
    pharmacy_id = current_user.get("pharmacy_id")
    if not pharmacy_id:
        raise HTTPException(status_code=400, detail="You do not have a registered pharmacy workspace to invite staff to.")

    # 2. Check if the user is already a member
    existing_user = await db["users"].find_one({"email": invite_data.email})
    if existing_user and existing_user.get("pharmacy_id"):
        raise HTTPException(status_code=400, detail=f"User {invite_data.email} already belongs to a workspace.")

    # 3. Check for existing pending invites
    existing_invite = await db["invitations"].find_one({
        "email": invite_data.email,
        "status": "pending"
    })
    
    if existing_invite:
        return {"message": f"An invitation is already pending for {invite_data.email}."}

    # 4. Create new invitation
    invitation = {
        "email": invite_data.email,
        "pharmacy_id": pharmacy_id,
        "invited_by": current_user["email"],
        "role": invite_data.role,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    
    await db["invitations"].insert_one(invitation)
    
    return {
        "status": "success",
        "message": f"Successfully invited {invite_data.email} to workspace {pharmacy_id}. Tell them to log in using Google Auth!"
    }
