from fastapi import APIRouter, Depends

from app.db import get_db
from app.modules.identity import service
from app.modules.identity.deps import get_current_user
from app.modules.identity.schemas import LoginRequest, ProfileUpdate

router = APIRouter(tags=["identity"])


@router.post("/api/auth/login")
def login(body: LoginRequest, db=Depends(get_db)):
    token, data = service.login(db, body.role, body.identity_key)
    return {"token": token, **data}


@router.get("/api/auth/identities")
def identities(db=Depends(get_db)):
    return service.list_identities(db)


@router.get("/api/me")
def me(user=Depends(get_current_user)):
    return service.user_profile(user)


@router.patch("/api/me")
def patch_me(body: ProfileUpdate, user=Depends(get_current_user), db=Depends(get_db)):
    return service.update_profile(db, user, body.model_dump(exclude_unset=True))