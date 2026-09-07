"""规则模拟器 API：用历史文档测试规则匹配效果（只读，不落库）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.services.rule_simulator import rule_simulator
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/rule-simulator", tags=["rule-simulator"])
logger = get_logger("api.rule_simulator")


class SimulateRequest(BaseModel):
    rule_type: str = Field(..., description='"filename"=文件名规则 / "keyword"=分类关键词规则')
    rule_ids: list[int] | None = Field(None, description="指定规则 id 列表，None=全部启用规则")
    category_id: int | None = Field(None, description="仅测试绑定到该分类的规则")
    limit: int = Field(200, description="测试集数量上限（最近处理优先），1~1000")


@router.post("/simulate")
def simulate(req: SimulateRequest, db=Depends(get_db)):
    """执行规则模拟，返回统计与明细（只读，不影响任何文件/数据）。"""
    return rule_simulator.simulate(
        db,
        rule_type=req.rule_type,
        rule_ids=req.rule_ids,
        category_id=req.category_id,
        limit=req.limit,
    )
