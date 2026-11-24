import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import app.models  # noqa: F401  注册全部表
from app import db
from app.config import settings
from app.core.clock import clock
from app.core.errors import DomainError
from app.core.idempotency import IdempotencyMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.Base.metadata.create_all(db.engine)
    if settings.ENV != "test":
        clock.load()
        from app.modules.scheduler.registry import register_jobs, scheduler

        register_jobs(scheduler)
        scheduler.start()
        if settings.ENV == "dev" and settings.SEED_ON_START:
            from app.seed import seed_if_empty

            seed_if_empty()
    yield
    if settings.ENV != "test":
        from app.modules.scheduler.registry import scheduler

        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    application = FastAPI(title="拾穗 Gleaners", version="0.1.0", lifespan=lifespan)
    application.add_middleware(IdempotencyMiddleware)

    @application.exception_handler(DomainError)
    async def _domain_error(request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code, content={"error": exc.message, "code": exc.code}
        )

    @application.get("/api/health", tags=["meta"])
    def health():
        return {"status": "ok", "env": settings.ENV, "pay_mode": settings.PAY_MODE}

    from app.modules.agent.router import router as agent_router
    from app.modules.commission.router import router as commission_router
    from app.modules.feed.router import router as feed_router
    from app.modules.identity.router import router as identity_router
    from app.modules.listing.router import router as listing_router
    from app.modules.matching.router import router as matching_router
    from app.modules.order.router import router as order_router
    from app.modules.payment.router import router as payment_router
    from app.modules.pickup.router import router as pickup_router

    application.include_router(identity_router)
    application.include_router(feed_router)
    application.include_router(agent_router)
    application.include_router(commission_router)
    application.include_router(listing_router)
    application.include_router(matching_router)
    application.include_router(order_router)
    application.include_router(payment_router)
    application.include_router(pickup_router)

    if settings.ENV == "dev":
        from app.modules.demo.router import router as demo_router

        application.include_router(demo_router)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)
    application.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return application


app = create_app()