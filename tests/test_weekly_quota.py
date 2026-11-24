"""周/月额度：用完仍 active / 硬过滤跳过 / reset 恢复 / 周期守卫幂等。"""
from datetime import timedelta

from app import db
from app.core.clock import clock
from app.models import Commission, Listing
from app.modules.commission import service as commission_service
from app.modules.matching import filters
from app.modules.scheduler import jobs
from tests.conftest import mk_commission, mk_listing, mk_merchant, mk_user


def _eval(cid=1, lid=1):
    with db.session_scope() as s:
        return filters.evaluate(s, s.get(Commission, cid), s.get(Listing, lid))


def test_weekly_used_still_active_and_skip(client):
    mk_user()
    mk_merchant()
    mk_commission(1, weekly_count_max=1, used_count_week=1)
    mk_listing()
    with db.session_scope() as s:
        commission_service.check_completion(s, s.get(Commission, 1))
        s.commit()
    with db.session_scope() as s:
        assert s.get(Commission, 1).status == "active"  # 周次数用完不改状态
    assert _eval()[0] is False  # 硬过滤跳过


def test_weekly_reset_recovers_and_guard(client):
    mk_user()
    mk_merchant()
    mk_commission(1, weekly_count_max=1, used_count_week=1,
                  valid_to=clock.now() + timedelta(days=30))
    mk_listing()
    assert _eval()[0] is False
    jobs.weekly_reset()  # 首次基线，不重置当期
    with db.session_scope() as s:
        assert s.get(Commission, 1).used_count_week == 1
    clock.advance(7 * 24 * 3600)  # 跨周 → 重置恢复
    jobs.weekly_reset()
    with db.session_scope() as s:
        assert s.get(Commission, 1).used_count_week == 0
    assert _eval()[0] is True
    # 周期守卫：同一周内重复调用不再重置
    with db.session_scope() as s:
        s.get(Commission, 1).used_count_week = 1
        s.commit()
    jobs.weekly_reset()
    with db.session_scope() as s:
        assert s.get(Commission, 1).used_count_week == 1
    clock.advance(7 * 24 * 3600)
    jobs.weekly_reset()
    with db.session_scope() as s:
        assert s.get(Commission, 1).used_count_week == 0


def test_monthly_reset_guard(client):
    mk_user()
    mk_commission(1, spent_amount_month=800)
    jobs.monthly_reset()  # 首次基线
    with db.session_scope() as s:
        assert s.get(Commission, 1).spent_amount_month == 800
    clock.advance(32 * 24 * 3600)  # 跨月 → 重置
    jobs.monthly_reset()
    with db.session_scope() as s:
        assert s.get(Commission, 1).spent_amount_month == 0
    with db.session_scope() as s:
        s.get(Commission, 1).spent_amount_month = 800
        s.commit()
    jobs.monthly_reset()
    with db.session_scope() as s:
        assert s.get(Commission, 1).spent_amount_month == 800  # 同月不重复重置