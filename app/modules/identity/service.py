from app.core.errors import ValidationFailed
from app.core.security import create_token
from app.models import Merchant, User

CENTER = (30.2741, 120.1551)


def user_profile(u: User) -> dict:
    return {
        "id": u.id,
        "nickname": u.nickname,
        "home_lat": u.home_lat,
        "home_lng": u.home_lng,
        "allergens": u.allergens,
        "diet_tags": u.diet_tags,
        "wallet_single_limit": u.wallet_single_limit,
        "credit_score": u.credit_score,
        "no_show_streak": u.no_show_streak,
        "win_count_7d": u.win_count_7d,
    }


def merchant_profile(m: Merchant) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "address": m.address,
        "lat": m.lat,
        "lng": m.lng,
        "rating": m.rating,
        "rating_count": m.rating_count,
        "pay_channel": m.pay_channel,
    }


def login(db, role: str, identity_key: str):
    identity_key = (identity_key or "").strip()
    if not identity_key:
        raise ValidationFailed("identity_key 不能为空")
    if role == "user":
        u = db.query(User).filter_by(nickname=identity_key).first()
        if not u:
            u = User(
                nickname=identity_key,
                home_lat=CENTER[0],
                home_lng=CENTER[1],
                allergens=[],
                wallet_single_limit=1500,
            )
            db.add(u)
            db.commit()
            db.refresh(u)
        return create_token(u.id, "user"), {"role": "user", "profile": user_profile(u)}
    if role == "merchant":
        m = db.query(Merchant).filter_by(name=identity_key).first()
        if not m:
            m = Merchant(name=identity_key, lat=CENTER[0], lng=CENTER[1])
            db.add(m)
            db.commit()
            db.refresh(m)
        return create_token(m.id, "merchant", m.id), {
            "role": "merchant",
            "profile": merchant_profile(m),
        }
    raise ValidationFailed("role 必须为 user 或 merchant")


def update_profile(db, u: User, patch: dict) -> dict:
    for k, v in patch.items():
        if v is not None:
            setattr(u, k, v)
    db.commit()
    db.refresh(u)
    return user_profile(u)


def list_identities(db) -> dict:
    users = db.query(User).order_by(User.id).all()
    merchants = db.query(Merchant).order_by(Merchant.id).all()
    return {
        "users": [{"id": u.id, "nickname": u.nickname} for u in users],
        "merchants": [{"id": m.id, "name": m.name} for m in merchants],
    }