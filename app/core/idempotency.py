import hashlib
import json

from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app import db
from app.models.auxiliary import IdempotencyRecord


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """请求级幂等：仅拦截带 Idempotency-Key 的 POST/PATCH（回调走业务级幂等，跳过）。"""

    async def dispatch(self, request, call_next):
        if request.method not in ("POST", "PATCH"):
            return await call_next(request)
        key = request.headers.get("Idempotency-Key")
        if not key or request.url.path.startswith("/callback"):
            return await call_next(request)

        body = await request.body()
        endpoint = request.url.path
        h = hashlib.sha256(request.method.encode() + endpoint.encode() + body).hexdigest()

        with db.session_scope() as s:
            rec = (
                s.query(IdempotencyRecord)
                .filter_by(endpoint=endpoint, key=key)
                .first()
            )
            if rec:
                if rec.request_hash != h:
                    return JSONResponse(
                        status_code=409,
                        content={"error": "Idempotency-Key 被用于不同请求体"},
                    )
                if rec.status == "done":
                    return Response(
                        content=rec.response_body,
                        status_code=rec.response_status,
                        media_type="application/json",
                    )
                return JSONResponse(status_code=409, content={"error": "请求处理中"})
            try:
                s.add(
                    IdempotencyRecord(
                        endpoint=endpoint, key=key, request_hash=h, status="processing"
                    )
                )
                s.commit()
            except IntegrityError:
                s.rollback()
                return JSONResponse(status_code=409, content={"error": "请求处理中"})

        response = await call_next(request)
        chunks = [chunk async for chunk in response.body_iterator]
        resp_body = b"".join(chunks)

        with db.session_scope() as s:
            rec = (
                s.query(IdempotencyRecord)
                .filter_by(endpoint=endpoint, key=key)
                .first()
            )
            if rec:
                rec.status = "done"
                rec.response_status = response.status_code
                rec.response_body = resp_body.decode("utf-8")
                s.commit()

        return Response(
            content=resp_body,
            status_code=response.status_code,
            media_type=response.media_type,
        )