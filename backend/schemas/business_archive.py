"""业务档案相关 Pydantic V2 模型（V2.0）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BusinessFileOut(BaseModel):
    """档案-文件关联输出（含文档冗余信息，便于列表直接展示）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: int
    document_id: int
    file_role: str
    is_primary: bool
    link_source: str
    sort_order: int
    created_at: datetime
    # 冗余字段由 API 层 join documents 填充
    document_filename: str = ""
    document_type: str = ""


class BusinessRecordOut(BaseModel):
    """业务档案输出（列表/详情通用，详情带 files）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_no: str
    title: str
    business_type: str
    status: str
    ship_name: str
    counterparty: str
    total_amount: float = 0.0
    sign_date: Optional[date] = None
    extra_data: str = "{}"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 关联文件数量统计（列表端点必带）
    file_count: int = 0
    # 详情端点带出文件列表
    files: list[BusinessFileOut] = Field(default_factory=list)


class BusinessRecordListResponse(BaseModel):
    ok: bool = True
    total: int = 0
    items: list[BusinessRecordOut] = Field(default_factory=list)


class BusinessRecordDetailResponse(BaseModel):
    ok: bool = True
    data: BusinessRecordOut


class BusinessRecordCreate(BaseModel):
    """手动创建档案请求。"""

    business_no: str = Field(..., min_length=1, max_length=255, description="业务编号/合同号")
    title: Optional[str] = None
    business_type: Optional[str] = None
    status: Optional[str] = None
    ship_name: Optional[str] = None
    counterparty: Optional[str] = None
    total_amount: Optional[float] = None
    sign_date: Optional[date] = None
    extra_data: Optional[str] = None


class BusinessRecordUpdate(BaseModel):
    """更新档案（全部可选，只更新传入字段）。"""

    title: Optional[str] = None
    business_type: Optional[str] = None
    status: Optional[str] = None
    ship_name: Optional[str] = None
    counterparty: Optional[str] = None
    total_amount: Optional[float] = None
    sign_date: Optional[date] = None
    extra_data: Optional[str] = None


class BusinessFileAddRequest(BaseModel):
    """手动把文档加入档案请求。"""

    document_id: int
    file_role: Optional[str] = None  # 缺省时按文档类型自动映射


class BackfillResponse(BaseModel):
    ok: bool = True
    stats: dict = Field(default_factory=dict)
