from fastapi import APIRouter, Depends

from app.db import get_db
from app.modules.identity.deps import get_current_merchant
from app.modules.listing import service

router = APIRouter(tags=["listing"])


@router.post("/api/merchant/listings")
def publish(body: dict, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.publish(db, merchant, body)


@router.get("/api/merchant/listings")
def list_mine(status: str | None = None, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return {"items": service.list_mine(db, merchant, status)}


@router.get("/api/merchant/demand")
def demand(merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.demand(db, merchant)


@router.get("/api/merchant/listings/{lid}")
def get_one(lid: int, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.to_dict(db, service._owned(db, merchant, lid))


@router.post("/api/merchant/listings/{lid}/off_shelf")
def off_shelf(lid: int, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.off_shelf(db, merchant, lid)


@router.post("/api/merchant/listings/{lid}/out_of_stock")
def out_of_stock(lid: int, merchant=Depends(get_current_merchant), db=Depends(get_db)):
    return service.out_of_stock(db, merchant, lid)