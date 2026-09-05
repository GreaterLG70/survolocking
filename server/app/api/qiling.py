"""起零数据查询代理。

端侧不直接请求第三方，统一走云端代理，便于缓存、限流与凭据保护。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models import User
from app.schemas.common import ok
from app.schemas.schemas import QilingQueryRequest
from app.services import qiling_service

router = APIRouter(prefix="/qiling", tags=["起零数据"])


@router.post("/query")
async def query(req: QilingQueryRequest, user: User = Depends(get_current_user)):
    """
    查询号码风险等级。

    起零实测响应 ~3.7s（已缓存命中则 <100ms）。端侧实时拦截主链路
    **不直接等待本接口**：第2层采用「本地缓存命中 + 后台预热」策略，
    本接口的调用方为端侧后台预热任务与手动查询界面，允许等待数秒。
    """
    result = await qiling_service.query(req.phone)
    return ok(result)
