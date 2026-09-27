from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.dependencies import DatabaseSession, credentials_error, get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User, UserRole
from app.schemas.auth import TokenResponse, UserPublic, UserRegister, normalize_email

router = APIRouter(prefix="/auth", tags=["Authentication"])
# Unknown accounts still perform an Argon2 verification.
_dummy_hash = hash_password("dummy-password-for-login-timing")


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: DatabaseSession) -> User:
    if payload.role == UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin self-registration is not allowed")
    try:
        if db.scalar(select(User).where(User.email == payload.email)) is not None:
            raise HTTPException(status_code=409, detail="Email is already registered")
        user = User(
            name=payload.name,
            email=payload.email,
            password_hash=hash_password(payload.password),
            role=payload.role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        # The unique index also protects against concurrent registrations.
        if getattr(exc.orig, "sqlstate", None) == "23505" and getattr(
            getattr(exc.orig, "diag", None), "constraint_name", None
        ) == "ix_users_email":
            raise HTTPException(status_code=409, detail="Email is already registered") from None
        raise HTTPException(status_code=500, detail="Unable to register user") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to register user") from None
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DatabaseSession
) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == normalize_email(form.username)))
    valid = verify_password(form.password, user.password_hash if user else _dummy_hash)
    if user is None or not valid:
        raise credentials_error()
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserPublic)
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user
