"""Authentication endpoints for user registration and login."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wealthdock_server.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from wealthdock_server.db.models import User
from wealthdock_server.db.session import get_db
from wealthdock_server.schemas.auth import Token, UserLogin, UserRegister

router = APIRouter()

# Dummy hash to prevent timing attacks on non-existent users
DUMMY_HASH = "$2b$12$Ke/xOMv5kCen85Dsbh0xhu9R8a9W8r0k77J3gS4v2X6.F5Y8t5L6O"


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    user_in: UserRegister,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Token:
    """Register a new user account.

    To defend against user enumeration, this endpoint returns a 201 Created
    response with a non-working token when the email address is already registered.
    """
    result = await db.execute(select(User).where(User.email == user_in.email))
    user = result.scalar_one_or_none()
    if user:
        # Perform password hashing to match timing of actual registration
        # and defend against timing attacks
        get_password_hash(user_in.password)
        dummy_id = uuid.uuid4()
        access_token = create_access_token(dummy_id)
        return Token(access_token=access_token, token_type="bearer", email=user_in.email)

    db_user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
    )
    db.add(db_user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Handle race condition collision without leaking user existence
        get_password_hash(user_in.password)
        dummy_id = uuid.uuid4()
        access_token = create_access_token(dummy_id)
        return Token(access_token=access_token, token_type="bearer", email=user_in.email)

    await db.refresh(db_user)

    access_token = create_access_token(db_user.id)
    return Token(access_token=access_token, token_type="bearer", email=db_user.email)


@router.post("/login", response_model=Token)
async def login(
    user_in: UserLogin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Token:
    """Authenticate email and password, return JWT token."""
    result = await db.execute(select(User).where(User.email == user_in.email))
    user = result.scalar_one_or_none()

    if not user:
        # Perform password verification against dummy hash to prevent timing attacks
        verify_password(user_in.password, DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
        )

    access_token = create_access_token(user.id)
    return Token(access_token=access_token, token_type="bearer", email=user.email)
