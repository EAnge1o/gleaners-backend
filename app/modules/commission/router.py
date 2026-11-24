from fastapi import APIRouter, Depends

from app.db import get_db
from app.modules.commission import service
from app.modules.commission.schemas import PatchDraft
from app.modules.identity.deps import get_current_user

router = APIRouter(tags=["commission"])


@router.get("/api/commissions")
def list_commissions(status: str | None = None, user=Depends(get_current_user), db=Depends(get_db)):
    return {"items": service.list_mine(db, user, status)}


@router.get("/api/me/quota")
def my_quota(user=Depends(get_current_user), db=Depends(get_db)):
    return service.quota(db, user)


@router.get("/api/commissions/{cid}")
def get_commission(cid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.get_mine(db, user, cid)


@router.patch("/api/commissions/{cid}")
def patch_commission(cid: int, body: PatchDraft, user=Depends(get_current_user), db=Depends(get_db)):
    return service.patch_draft(db, user, cid, body.model_dump(exclude_unset=True))


@router.post("/api/commissions/{cid}/authorize")
def authorize(cid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.authorize(db, user, cid)


@router.post("/api/commissions/{cid}/pause")
def pause(cid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.pause(db, user, cid)


@router.post("/api/commissions/{cid}/resume")
def resume(cid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.resume(db, user, cid)


@router.post("/api/commissions/{cid}/cancel")
def cancel(cid: int, user=Depends(get_current_user), db=Depends(get_db)):
    return service.cancel(db, user, cid)
