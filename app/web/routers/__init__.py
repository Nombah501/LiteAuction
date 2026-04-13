from fastapi import FastAPI


def include_all_routers(app: FastAPI) -> None:
    from app.web.routers.auth_routes import router as auth_router
    from app.web.routers.dashboard import router as dashboard_router
    from app.web.routers.auctions import router as auctions_router
    from app.web.routers.complaints import router as complaints_router
    from app.web.routers.signals import router as signals_router
    from app.web.routers.trade_feedback import router as trade_feedback_router
    from app.web.routers.appeals import router as appeals_router
    from app.web.routers.users import router as users_router
    from app.web.routers.roles import router as roles_router
    from app.web.routers.violators import router as violators_router
    from app.web.routers.presets import router as presets_router
    from app.web.routers.telemetry import router as telemetry_router
    from app.web.routers.triage import router as triage_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(auctions_router)
    app.include_router(complaints_router)
    app.include_router(signals_router)
    app.include_router(trade_feedback_router)
    app.include_router(appeals_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(violators_router)
    app.include_router(presets_router)
    app.include_router(telemetry_router)
    app.include_router(triage_router)
