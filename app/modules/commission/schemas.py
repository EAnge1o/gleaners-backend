from typing import Literal

from pydantic import BaseModel, Field


class PatchDraft(BaseModel):
    """草稿修改：仅传需要改的字段（exclude_unset），合并后重过 RuleCard 闸门。"""

    buy_modes: list[Literal["blind", "public"]] | None = None
    categories: list[str] | None = None
    size: str | None = None
    merchant_ids: list[int] | None = None
    radius_m: int | None = Field(None, ge=500, le=3000)
    active_weekdays: list[int] | None = None
    active_time_start: str | None = None
    active_time_end: str | None = None
    per_order_max: int | None = Field(None, gt=0)
    weekly_count_max: int | None = Field(None, ge=1)
    monthly_amount_max: int | None = Field(None, gt=0)
    valid_days: int | None = Field(None, ge=1, le=90)
