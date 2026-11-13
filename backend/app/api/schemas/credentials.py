"""Credential models: status, save and test of the stored provider keys."""

from datetime import datetime

from pydantic import BaseModel, Field


class CredentialStatusOut(BaseModel):
    kind: str
    label: str
    description: str
    configured: bool
    last_four: str | None
    updated_at: datetime | None
    last_tested_at: datetime | None
    last_test_result: str | None


class CredentialSave(BaseModel):
    value: str = Field(min_length=8, max_length=500)


class CredentialTestOut(BaseModel):
    ok: bool
    message: str
