from datetime import datetime, timedelta, timezone


class Clock:
    """虚拟时钟：所有业务时间判定的唯一来源。offset 持久化到 app_meta。"""

    def __init__(self):
        self._offset = 0

    def now(self) -> datetime:
        # 内部统一 naive UTC
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            seconds=self._offset
        )

    @property
    def offset(self) -> int:
        return self._offset

    def advance(self, seconds: int):
        self._offset += int(seconds)
        self._save()

    def reset(self):
        self._offset = 0
        self._save()

    def load(self):
        from app import db
        from app.models.auxiliary import AppMeta

        try:
            with db.session_scope() as s:
                m = s.get(AppMeta, "clock_offset")
                if m:
                    self._offset = int(m.value)
        except Exception:
            pass

    def _save(self):
        from app import db
        from app.models.auxiliary import AppMeta

        try:
            with db.session_scope() as s:
                m = s.get(AppMeta, "clock_offset")
                if m:
                    m.value = str(self._offset)
                else:
                    s.add(AppMeta(key="clock_offset", value=str(self._offset)))
                s.commit()
        except Exception:
            pass


clock = Clock()