"""Backup models."""

from datetime import datetime

from pydantic import BaseModel


class BackupOut(BaseModel):
    name: str
    size: int
    created_at: datetime
