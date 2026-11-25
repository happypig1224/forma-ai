"""Pydantic schemas for authentication requests and responses."""

from datetime import datetime

import email_validator
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Enable RFC 2606 and RFC 6761 special-use domains (.test, .example)
email_validator.TEST_ENVIRONMENT = True


class TokenResponse(BaseModel):
    """Access token payload returned upon successful authentication or refresh."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(gt=0)


class RegisterRequest(BaseModel):
    """Payload to register a new user account with unique email and password."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """Payload to authenticate an existing user account."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    """Public user identity information."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    email: str
    role: str
    status: str
    created_at: datetime
