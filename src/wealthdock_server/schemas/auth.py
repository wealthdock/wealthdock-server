"""Pydantic schemas for auth endpoints."""

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserRegister(BaseModel):
    """Schema for user registration requests."""

    email: EmailStr = Field(..., description="User's email address.")
    password: str = Field(
        ...,
        min_length=8,
        description="Password must be between 8 characters and 72 bytes long.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        """Normalize email address to lowercase and strip whitespace."""
        if isinstance(v, str):
            return v.lower().strip()
        return v

    @field_validator("password")
    @classmethod
    def validate_password_byte_length(cls, v: str) -> str:
        """Ensure password does not exceed bcrypt's 72-byte limit."""
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password cannot be longer than 72 bytes")
        return v


class UserLogin(BaseModel):
    """Schema for user login requests."""

    email: EmailStr = Field(..., description="User's email address.")
    password: str = Field(
        ...,
        min_length=8,
        description="Password must be between 8 characters and 72 bytes long.",
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        """Normalize email address to lowercase and strip whitespace."""
        if isinstance(v, str):
            return v.lower().strip()
        return v

    @field_validator("password")
    @classmethod
    def validate_password_byte_length(cls, v: str) -> str:
        """Ensure password does not exceed bcrypt's 72-byte limit."""
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password cannot be longer than 72 bytes")
        return v


class Token(BaseModel):
    """Schema representing an authentication token response."""

    access_token: str
    token_type: str
    email: str
