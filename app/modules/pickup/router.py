from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_db
from app.modules.identity.deps import get_current_merchant
from app.modules.pickup import service

router = APIRouter(tags=["pickup"])


class VerifyByCode(BaseModel):
    code: str


@router.post("/api/merchant/orders/{oid}/verify")
def verify_order(oid: int, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.verify(db, merchant, order_id=oid)


@router.post("/api/merchant/verify-code")
def verify_code(body: VerifyByCode, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.verify(db, merchant, code=body.code.strip())