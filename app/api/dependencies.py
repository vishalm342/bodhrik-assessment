import re
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
DatabaseSession = Annotated[Session, Depends(get_db)]


def credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)], db: DatabaseSession
) -> User:
    if not token:
        raise credentials_error()
    try:
        subject = decode_access_token(token)["sub"]
        # Match the existing PostgreSQL positive signed 32-bit integer ID.
        if not isinstance(subject, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", subject):
            raise credentials_error()
        user_id = int(subject)
        if user_id > 2_147_483_647:
            raise credentials_error()
    except InvalidTokenError:
        raise credentials_error() from None
    user = db.get(User, user_id)
    if user is None:
        raise credentials_error()
    return user
