from pydantic import BaseModel


class LoginRequest(BaseModel):
    role: str  # user / merchant
    identity_key: str  # 用户昵称或商家名


class ProfileUpdate(BaseModel):
    nickname: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    allergens: list[str] | None = None
    diet_tags: list[str] | None = None
    wallet_single_limit: int | None = None