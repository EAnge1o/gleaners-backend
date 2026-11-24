from fastapi import APIRouter, Depends

from app.db import get_db
from app.modules.identity.deps import get_current_merchant, get_current_user
from app.modules.order import service

router = APIRouter(tags=["order"])


@router.get("/api/orders")
def list_mine(status: str | None = None, user=Depends(get_current_user), db=Depends(get_db)):
    return {"items": service.list_mine(db, user, status)}


@router.get("/api/orders/{oid}")
def get_mine(oid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.get_mine(db, user, oid)


@router.get("/api/merchant/orders")
def list_merchant(status: str | None = None, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return {"items": service.list_merchant(db, merchant, status)}


@router.post("/api/orders/{oid}/confirm")
def confirm(oid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.confirm(db, user, oid)


@router.post("/api/orders/{oid}/reject")
def reject(oid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.reject(db, user, oid)