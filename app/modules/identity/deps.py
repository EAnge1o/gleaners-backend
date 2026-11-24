from fastapi import Depends, Header

from app.core.errors import Forbidden, NotFound
from app.core.security import decode_token
from app.db import get_db
from app.models import Merchant, User


def _payload(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise Forbidden("缺少 Bearer 令牌")
    try:
        return decode_token(authorization[7:])
    except Exception:
        raise Forbidden("令牌无效或已过期")


def get_current_user(authorization: str = Header(None), db=Depends(get_db)) -> User:
    p = _payload(authorization)
    if p.get("role") != "user":
        raise Forbidden("需要用户身份")
    u = db.get(User, int(p["sub"]))
    if not u:
        raise NotFound("用户不存在")
    return u


def get_current_merchant(authorization: str = Header(None), db=Depends(get_db)) -> Merchant:
    p = _payload(authorization)
    if p.get("role") != "merchant":
        raise Forbidden("需要商家身份")
    m = db.get(Merchant, int(p["sub"]))
    if not m:
        raise NotFound("商家不存在")
    return m