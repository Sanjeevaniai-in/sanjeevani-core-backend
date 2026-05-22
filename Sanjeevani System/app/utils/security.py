import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
security = HTTPBearer()

# Clock-skew tolerance – accepts tokens that expired up to 30 s ago.
_JWT_LEEWAY = timedelta(seconds=30)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Mint a new signed JWT.

    Parameters
    ----------
    data          : Payload dict.  ``sub`` should be the user/merchant identity.
    expires_delta : Override default expiration (settings.JWT_EXPIRATION_HOURS).

    Returns
    -------
    Signed JWT string.
    """
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    )
    payload = {**data, "exp": expire, "iat": datetime.now(tz=timezone.utc)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_jwt_token(token: str) -> dict:
    """
    Verify and decode a JWT token.

    Checks
    ------
    - Signature validity (secret + algorithm).
    - Expiration (with 30-second leeway for clock skew).
    - Presence of ``exp`` claim.

    Returns
    -------
    Decoded payload dict.

    Raises
    ------
    HTTPException 401 on any validation failure.
    """
    try:
        payload: dict = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            leeway=_JWT_LEEWAY,
            options={"require": ["exp"]},   # force 'exp' claim to be present
        )

        # Extra explicit check: ensure expiry is a future timestamp
        exp_ts = payload.get("exp")
        if exp_ts is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing expiration claim.",
            )
        if datetime.fromtimestamp(exp_ts, tz=timezone.utc) < (
            datetime.now(tz=timezone.utc) - _JWT_LEEWAY
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
            )

        identity = (
            payload.get("pharmacy_id")
            or payload.get("merchant_id")
            or payload.get("sub")
            or payload.get("email", "unknown")
        )
        logger.debug("JWT verified", extra={"identity": identity})
        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("JWT expired token rejected.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        )
    except jwt.MissingRequiredClaimError as exc:
        logger.warning("JWT missing claim", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: missing claim ({exc}).",
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT invalid token", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    FastAPI dependency – extracts and validates the current user from JWT.
    Safe to use across all Sanjeevani System routes.
    """
    token = credentials.credentials
    user_data = verify_jwt_token(token)

    identity_value = (
        user_data.get("pharmacy_id")
        or user_data.get("merchant_id")
        or user_data.get("sub")
        or user_data.get("email", "unknown")
    )

    user_data["pharmacy_id"] = identity_value
    user_data["merchant_id"] = identity_value

    return user_data
