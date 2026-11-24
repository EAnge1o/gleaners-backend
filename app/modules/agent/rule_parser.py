"""确定性兜底解析器：正则/关键词抽取。LLM 不可用或输出非法时启用，永远可用。

约定：口述时间（如「今晚 20:00」）按虚拟时钟当前日期解释（内部统一 naive UTC），
已过时刻自动顺延到次日，保证演示场景取货窗口始终在未来。
"""
import re
from datetime import datetime, time, timedelta

from app.core.clock import clock
from app.core.utils import yuan_to_fen
from app.models import Merchant

CATEGORY_KEYWORDS = {
    "面包烘焙": ["面包", "烘焙", "吐司", "可颂", "贝果", "欧包"],
    "餐食": ["便当", "餐食", "盒饭", "快餐", "米饭", "套餐", "轻食"],
    "生鲜": ["生鲜", "蔬菜", "水果", "沙拉", "菜品"],
    "甜品": ["甜品", "蛋糕", "甜点", "糕点"],
    "饮品": ["饮品", "奶茶", "咖啡", "果汁"],
}
KNOWN_ALLERGENS = ["花生", "麸质", "牛奶", "鸡蛋", "坚果", "大豆", "海鲜", "芝麻"]
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_TIME_RANGE = re.compile(
    r"(晚上|下午|中午|早上|上午|晚)?\s*(\d{1,2})\s*[点:：]\s*(\d{2})?\s*"
    r"(?:到|至|-|~|—|–)\s*"
    r"(晚上|下午|中午|早上|上午|晚)?\s*(\d{1,2})\s*[点:：]\s*(\d{2})?"
)


def _categories(text: str) -> list[str]:
    return [c for c, kws in CATEGORY_KEYWORDS.items() if any(k in text for k in kws)]


def _to_hour(period: str | None, h: int) -> int:
    if period in ("晚上", "下午", "晚") and h < 12:
        return h + 12
    return h


def _parse_time_range(text: str) -> tuple[str | None, str | None]:
    m = _TIME_RANGE.search(text)
    if not m:
        return None, None
    p1, h1, m1, p2, h2, m2 = m.groups()
    h1, h2 = _to_hour(p1, int(h1)), _to_hour(p2, int(h2))
    if p2 is None and p1 in ("晚上", "下午", "晚") and h2 < 12:
        h2 += 12
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23):
        return None, None
    return f"{h1:02d}:{m1 or '00'}", f"{h2:02d}:{m2 or '00'}"


def _pickup_window(text: str) -> tuple[datetime | None, datetime | None]:
    s, e = _parse_time_range(text)
    if not s:
        return None, None
    now = clock.now()
    day = now.date()
    tomorrow = "明天" in text or "明日" in text
    if tomorrow:
        day += timedelta(days=1)
    start = datetime.combine(day, time.fromisoformat(s))
    end = datetime.combine(day, time.fromisoformat(e))
    if end <= start:
        end += timedelta(days=1)  # 跨午夜取货窗
    if start <= now and not tomorrow:
        start += timedelta(days=1)
        end += timedelta(days=1)
    return start, end


def _valid_days(text: str) -> int:
    m = re.search(r"(?:有效期|管|接下来|未来)\s*(\d+|[一二两三四五六七八九十]+)\s*天", text)
    if not m:
        m = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*天", text)
    if m:
        g = m.group(1)
        days = int(g) if g.isdigit() else _CN_NUM.get(g, 7)
    elif "一个月" in text:
        days = 30
    else:
        days = 7
    return max(1, min(90, days))


def parse_user_rule(db, user, text: str) -> tuple[dict, list[str]]:
    """返回 (RuleCard 数据, 追问列表)。追问非空时调用方不得入库。"""
    questions: list[str] = []
    data: dict = {}
    t = text

    # 月额度先抽取并从文本移除，避免被误认为每单金额
    m = re.search(r"(?:每月|月额度|月度|一个月)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(?:元|块)", t)
    if m:
        data["monthly_amount_max"] = yuan_to_fen(m.group(1))
        t = t.replace(m.group(0), " ")
    # 周次数
    m = re.search(r"(?:每周|一周|每星期)[^0-9]{0,6}(\d+)\s*(?:次|单|份)", t)
    if m:
        data["weekly_count_max"] = int(m.group(1))
        t = t.replace(m.group(0), " ")
    # 每单金额（必填）
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)", t)
    if not m:
        m = re.search(r"(?:不超过|以内|以下|上限|预算)\s*(\d+(?:\.\d+)?)", t)
    if m:
        amount = yuan_to_fen(m.group(1))
        if amount <= 0:
            questions.append("每单金额须大于 0 元，请重新说明预算。")
        else:
            data["per_order_max"] = amount
    else:
        questions.append("每单最多花多少钱？例如：每单不超过 15 元。")

    # 半径（米/公里），clamp 到 [500, 3000]
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公里|千米|km|KM|米|m)", t)
    if m:
        val = float(m.group(1))
        meters = val * 1000 if m.group(2) in ("公里", "千米", "km", "KM") else val
        data["radius_m"] = max(500, min(3000, int(round(meters))))

    cats = _categories(text)
    if cats:
        data["categories"] = cats

    blind, public = "盲盒" in text, "公开" in text
    if blind and not public:
        data["buy_modes"] = ["blind"]
    elif public and not blind:
        data["buy_modes"] = ["public"]
    else:
        data["buy_modes"] = ["blind", "public"]

    if re.search(r"单次|一次性|就买一次|只买一次|一次就好", text):
        data["type"] = "single"

    if "工作日" in text:
        data["active_weekdays"] = [0, 1, 2, 3, 4]
    elif "周末" in text:
        data["active_weekdays"] = [5, 6]

    s, e = _parse_time_range(text)
    if s:
        data["active_time_start"], data["active_time_end"] = s, e

    data["valid_days"] = _valid_days(text)

    ids = [m.id for m in db.query(Merchant).all() if m.name and m.name in text]
    if ids:
        data["merchant_ids"] = ids

    return data, questions


def parse_merchant_listing(db, merchant, text: str) -> tuple[dict, list[str]]:
    """返回 (ListingDraft 数据, 追问列表)。追问非空时调用方不得入库。"""
    questions: list[str] = []
    mode = "blind" if "盲盒" in text else "public"

    cats = _categories(text)
    if not cats:
        questions.append("余量属于哪个品类？（面包烘焙/餐食/生鲜/甜品/饮品）")
    category = cats[0] if cats else ""

    t = text
    m = re.search(r"(?:原价|价值|门市价)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", t)
    original = yuan_to_fen(m.group(1)) if m else None
    if m:
        t = t.replace(m.group(0), " ")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)", t)
    price = yuan_to_fen(m.group(1)) if m else None
    if price is None:
        questions.append("售价多少元？例如：12.9 元。")
    if mode == "blind" and original is None:
        questions.append("盲盒须标注原价，且不低于售价的 2.5 倍。")

    m = re.search(r"(\d+)\s*份", t) or re.search(r"限量\s*(\d+)", t)
    qty = int(m.group(1)) if m else None
    if qty is None:
        questions.append("一共几份？例如：共 3 份。")

    start, end = _pickup_window(text)
    if start is None:
        questions.append("取货时间窗是？例如：今晚 20:00-22:00。")

    items: list[str] = []
    m = re.search(r"(?:包含|内有|里面有)\s*([^，。；;]+)", t)
    if m:
        items = [x.strip() for x in re.split(r"[、+和/]", m.group(1)) if x.strip()]
    if not items:
        if mode == "blind":
            items = ["今日余量盲盒"]  # paid 后才揭晓
        elif category:
            items = [f"{category}余量"]

    if re.search(r"(?:无|不含|没有)\s*过敏原", text):
        allergens: list[str] = []
    else:
        allergens = [a for a in KNOWN_ALLERGENS if a in text]
        if mode == "blind" and not allergens:
            allergens = ["未知"]

    data = {
        "mode": mode,
        "category": category,
        "items": items,
        "allergens": allergens,
        "original_price": original if original is not None else (price or 0),
        "price": price or 0,
        "qty_total": qty or 0,
        "pickup_start": start,
        "pickup_end": end,
    }
    return data, questions
