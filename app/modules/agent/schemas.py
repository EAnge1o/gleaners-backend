"""Pydantic 校验闸门：RuleCard / ListingDraft。非法数据一律不入库。"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.errors import ValidationFailed

ALL_CATEGORIES = ["面包烘焙", "餐食", "生鲜", "甜品", "饮品"]
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_or_raise(model, data: dict):
    """schema 校验失败转成领域错误（422 + 可读文案），供 router/service 统一使用。"""
    try:
        return model.model_validate(data)
    except ValidationError as e:
        msgs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise ValidationFailed("；".join(msgs))


class RuleCard(BaseModel):
    """用户一句话规则的结构化卡片，与 Commission 列一一对应。"""

    type: Literal["periodic", "single"] = "periodic"
    buy_modes: list[Literal["blind", "public"]] = Field(
        default_factory=lambda: ["blind", "public"]
    )
    categories: list[str] = Field(default_factory=lambda: list(ALL_CATEGORIES))
    size: str = "不限"
    merchant_ids: list[int] = Field(default_factory=list)
    radius_m: int = Field(1000, ge=500, le=3000)
    active_weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    active_time_start: str | None = None  # "HH:MM"
    active_time_end: str | None = None
    per_order_max: int = Field(gt=0)  # 分
    weekly_count_max: int | None = Field(None, ge=1)
    monthly_amount_max: int | None = Field(None, gt=0)  # 分
    valid_days: int = Field(7, ge=1, le=90)

    @field_validator("buy_modes", "categories")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("active_weekdays")
    @classmethod
    def _weekdays(cls, v):
        if any(d not in range(7) for d in v):
            raise ValueError("星期取值须为 0-6（0=周一）")
        return sorted(set(v))

    @model_validator(mode="after")
    def _time_window(self):
        s, e = self.active_time_start, self.active_time_end
        if (s is None) != (e is None):
            raise ValueError("生效时段须同时给出起止时间")
        if s is not None:
            if not (_TIME_RE.match(s) and _TIME_RE.match(e)):
                raise ValueError("时间格式须为 HH:MM")
            if s >= e:
                raise ValueError("生效开始时间须早于结束时间")
        return self


class ListingDraft(BaseModel):
    """商家一句话发布的余量草稿。盲盒双门槛之一（价格比）在此执法。"""

    mode: Literal["blind", "public"]
    category: str
    size: str = "标准"
    diet_tags: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)  # 盲盒未知 = ["未知"]
    original_price: int = Field(gt=0)  # 分
    price: int = Field(gt=0)  # 分
    qty_total: int = Field(ge=1)
    pickup_start: datetime
    pickup_end: datetime

    @field_validator("category")
    @classmethod
    def _category(cls, v):
        if not v.strip():
            raise ValueError("品类不能为空")
        return v

    @model_validator(mode="after")
    def _gates(self):
        if self.pickup_end <= self.pickup_start:
            raise ValueError("取货结束时间须晚于开始时间")
        if self.mode == "blind":
            if self.original_price < self.price * 2.5:
                raise ValueError("盲盒原价须不低于售价的 2.5 倍")
        elif not self.items:
            raise ValueError("全公开余量须列明内容物")
        return self
