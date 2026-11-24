from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_db
from app.modules.agent import service
from app.modules.identity.deps import get_current_merchant, get_current_user

router = APIRouter(tags=["agent"])


class ParseRequest(BaseModel):
    text: str


@router.post("/api/agent/parse")
def parse_user_rule(body: ParseRequest, user=Depends(get_current_user), db=Depends(get_db)):
    return service.parse_user_rule(db, user, body.text)


@router.post("/api/merchant/listings/parse")
def parse_merchant_listing(
    body: ParseRequest, merchant=Depends(get_current_merchant), db=Depends(get_db)
):
    return service.parse_merchant_listing(db, merchant, body.text)
