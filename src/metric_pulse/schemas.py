from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    role: str


class DatasetSelection(BaseModel):
    sheet_name: str
    descriptor_fields: list[str] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    business_key_fields: list[str] = Field(default_factory=list)
    mode: str = "row_contract_collect"


class TaskCreate(BaseModel):
    file_id: str
    name: str
    datasets: list[DatasetSelection] = Field(default_factory=list)
    start_immediately: bool = False


class TaskControl(BaseModel):
    expected_version: int | None = None


class ReviewRequest(BaseModel):
    decision: str
    values: dict[str, Any] | None = None
    comment: str | None = None
    expected_version: int


class BulkReviewRequest(BaseModel):
    unit_ids: list[str]
    decision: str = "APPROVED"
    comment: str | None = None


class TemplateCreate(BaseModel):
    name: str
    file_id: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "VIEWER"
