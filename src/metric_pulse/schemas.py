"""HTTP 请求与响应使用的 Pydantic 数据契约。

这些模型只描述 API 边界，不承载数据库默认值或状态迁移。服务层仍需验证字段集合、版本号、
角色权限和决策是否符合当前资源状态。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """用户名/密码登录载荷。"""

    username: str
    password: str


class UserView(BaseModel):
    """可安全返回给前端的用户身份，不包含密码摘要。"""

    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    role: str


class DatasetSelection(BaseModel):
    """创建任务时对一个工作表声明的描述列、目标列与业务主键。"""

    sheet_name: str
    descriptor_fields: list[str] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    business_key_fields: list[str] = Field(default_factory=list)
    mode: str = "row_contract_collect"


class TaskCreate(BaseModel):
    """从已上传文件创建采集任务的请求。"""

    file_id: str
    name: str
    datasets: list[DatasetSelection] = Field(default_factory=list)
    start_immediately: bool = False


class TaskControl(BaseModel):
    """启动、暂停、恢复等控制操作使用的乐观锁版本。"""

    expected_version: int | None = None


class ReviewRequest(BaseModel):
    """单条审核决策；修订值仅在审核员明确提交时覆盖模型结果。"""

    decision: str
    values: dict[str, Any] | None = None
    comment: str | None = None
    expected_version: int


class BulkReviewPreviewRequest(BaseModel):
    """批量审核预检条件，不直接产生状态变更。"""

    unit_ids: list[str] = Field(default_factory=list)
    decision: str = "APPROVED"
    comment: str | None = None
    risk_levels: list[str] = Field(default_factory=list)


class BulkReviewCommitRequest(BaseModel):
    """使用预检返回的短期令牌提交同一批审核操作。"""

    preview_token: str


class ReviewPolicyCreate(BaseModel):
    """抽样审核策略及可接受错误率。"""

    name: str
    rules: dict[str, Any] = Field(default_factory=dict)
    sample_rate: float = Field(default=0.05, ge=0, le=1)
    max_sample_error_rate: float = Field(default=0.02, ge=0, le=1)


class TemplateCreate(BaseModel):
    """把已分析文件登记为可复用模板。"""

    name: str
    file_id: str


class UserCreate(BaseModel):
    """管理员创建本地账号的请求。"""

    username: str
    password: str
    role: str = "VIEWER"
