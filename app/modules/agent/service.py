"""解析编排：LLM → 确定性兜底 → Pydantic 闸门 → 建草稿 → 同事务推 rule_card 卡。
缺必填或非法 → 返回追问文案，不入库。
"""
from app.core.errors import ValidationFailed
from app.modules.agent import prompts, rule_parser
from app.modules.agent.llm_adapter import get_llm
from app.modules.agent.schemas import ListingDraft, RuleCard, validate_or_raise
from app.modules.commission import service as commission_service
from app.modules.feed.service import push_card


def _try_llm(system: str, text: str, model):
    llm = get_llm()
    if llm is None:
        return None
    data = llm.extract_json(system, text)
    if not data or data.get("questions"):
        return None
    try:
        return model.model_validate(data)
    except Exception:
        return None  # LLM 输出非法 → 降级兜底


def parse_user_rule(db, user, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValidationFailed("请输入一句话规则")

    card = _try_llm(prompts.USER_RULE_SYSTEM, text, RuleCard)
    if card is None:
        data, questions = rule_parser.parse_user_rule(db, user, text)
        if questions:
            return {"ok": False, "questions": questions, "raw_text": text}
        try:
            card = validate_or_raise(RuleCard, data)
        except ValidationFailed as e:
            return {"ok": False, "questions": [e.message], "raw_text": text}

    c = commission_service.create_draft(db, user, text, card)
    push_card(db, "rule_card", {
        "commission_id": c.id,
        "raw_text": text,
        "rule_card": card.model_dump(),
        "status": "draft",
    }, user_id=user.id)
    db.commit()
    db.refresh(c)
    return {"ok": True, "commission": commission_service.to_dict(c), "questions": []}


def parse_merchant_listing(db, merchant, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValidationFailed("请输入一句话余量描述")

    draft = _try_llm(prompts.MERCHANT_LISTING_SYSTEM, text, ListingDraft)
    if draft is None:
        data, questions = rule_parser.parse_merchant_listing(db, merchant, text)
        if questions:
            return {"ok": False, "questions": questions, "raw_text": text}
        try:
            draft = validate_or_raise(ListingDraft, data)
        except ValidationFailed as e:
            return {"ok": False, "questions": [e.message], "raw_text": text}

    payload = draft.model_dump(mode="json")
    push_card(db, "rule_card", {
        "raw_text": text, "draft": payload, "status": "draft",
    }, merchant_id=merchant.id)
    db.commit()
    return {"ok": True, "draft": payload, "questions": []}
