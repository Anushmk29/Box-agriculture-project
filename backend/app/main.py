from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import (
    admin,
    auth,
    handoffs,
    invoicing,
    master_data,
    operations,
    orders,
    packing,
    procurement,
    sku_mappings,
)
from app.core.config import settings
from app.core.database import get_db

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="3.0.0",
    description="Enterprise API for Two-Agent Autonomous Order-to-Cash & Packing System (Agriculture is Life Co., Ltd.)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers under /api/v3
app.include_router(auth.router, prefix=settings.API_V3_STR)
app.include_router(admin.router, prefix=settings.API_V3_STR)
app.include_router(master_data.router, prefix=settings.API_V3_STR)
app.include_router(orders.router, prefix=settings.API_V3_STR)
app.include_router(procurement.router, prefix=settings.API_V3_STR)
app.include_router(packing.router, prefix=settings.API_V3_STR)
app.include_router(invoicing.router, prefix=settings.API_V3_STR)
app.include_router(sku_mappings.router, prefix=settings.API_V3_STR)
app.include_router(handoffs.router, prefix=settings.API_V3_STR)
app.include_router(operations.router, prefix=settings.API_V3_STR)


@app.get("/")
def root():
    return {
        "platform": settings.PROJECT_NAME,
        "version": "v3.0",
        "client": "Agriculture is Life Co., Ltd.",
        "status": "OPERATIONAL",
        "docs_url": "/docs",
    }


@app.get("/health")
def health_check():
    return {"status": "HEALTHY"}


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database readiness check failed",
        ) from exc
    return {"status": "READY", "database": "reachable"}
