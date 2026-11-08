from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.api import (
    admin,
    auth,
    handoffs,
    invoicing,
    master_data,
    operations,
    order_exceptions,
    order_templates,
    orders,
    packing,
    packing_history,
    packing_setup,
    procurement,
    registers,
    sku_mappings,
)
from app.core.body_limits import BodyLimits
from app.core.config import settings
from app.core.database import get_db
from app.core.file_storage import StorageError, get_storage
from app.core.revisions import RevisionConflict
from app.core.work_limits import WorkLimit
from app.modules.pricing import PriceCatalogueReviewRequired

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="3.0.0",
    description="Enterprise API for Two-Agent Autonomous Order-to-Cash & Packing System (Agriculture is Life Co., Ltd.)",
)


@app.exception_handler(WorkLimit)
def work_limit(_request: Request, exc: WorkLimit) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": {
        "code": "WORK_BUDGET_EXCEEDED", "message": str(exc)}},
        headers={"Retry-After": "2"} if exc.status_code == 503 else {})


@app.exception_handler(DBAPIError)
def database_wait(_request: Request, exc: DBAPIError) -> JSONResponse:
    code = getattr(exc.orig, "pgcode", None)
    if code in {"55P03", "57014", "40P01", "40001"}:
        return JSONResponse(status_code=503, content={"detail": {"code": "DATABASE_WORK_INTERRUPTED",
            "message": "Database work was interrupted. Check the current record; retry protected submissions with their original key."}},
            headers={"Retry-After": "2"})
    return JSONResponse(status_code=500, content={"detail": "Database operation failed"})


@app.exception_handler(PoolTimeout)
def database_busy(_request: Request, _exc: PoolTimeout) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": {"code": "DATABASE_BUSY",
        "message": "The system is busy. Retry the original submission shortly."}}, headers={"Retry-After": "2"})


@app.exception_handler(StorageError)
def storage_unavailable(_request: Request, _exc: StorageError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": {"code": "STORAGE_UNAVAILABLE",
        "message": "Document storage is unavailable. Retry the original submission or contact an administrator."}})


@app.exception_handler(RevisionConflict)
def revision_conflict(_request: Request, exc: RevisionConflict) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(StaleDataError)
def concurrent_write(_request: Request, _exc: StaleDataError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": {"code": "STALE_REVISION",
        "message": "This record changed during your request. Your changes were not saved. Reload and review before trying again."}})

@app.exception_handler(PermissionError)
def permission_denied(_request: Request, exc: PermissionError) -> JSONResponse:
    """Module-level role checks raise PermissionError; surface them as 403, not 500."""
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})


@app.exception_handler(PriceCatalogueReviewRequired)
def catalogue_review_required(_request: Request, exc: PriceCatalogueReviewRequired) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT,
        content={"detail": {"code": "PRICE_CATALOGUE_REVIEW_REQUIRED", "message": str(exc)}})


@app.exception_handler(RequestValidationError)
def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Raw inputs can contain passwords or nonfinite JSON numbers. Return useful
    # field errors without reflecting secrets or failing to serialize NaN/Inf.
    errors = [{"type": error["type"],
               "loc": [part.encode("utf-8", errors="replace").decode("utf-8") if isinstance(part, str) else part
                       for part in error["loc"]],
               "msg": error["msg"].encode("utf-8", errors="replace").decode("utf-8")}
              for error in exc.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        content={"detail": errors})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BodyLimits)

# Mount API Routers under /api/v3
app.include_router(registers.router, prefix=settings.API_V3_STR)
app.include_router(auth.router, prefix=settings.API_V3_STR)
app.include_router(admin.router, prefix=settings.API_V3_STR)
app.include_router(master_data.router, prefix=settings.API_V3_STR)
app.include_router(orders.router, prefix=settings.API_V3_STR)
app.include_router(procurement.router, prefix=settings.API_V3_STR)
app.include_router(packing.router, prefix=settings.API_V3_STR)
app.include_router(packing_setup.router, prefix=settings.API_V3_STR)
app.include_router(packing_history.router, prefix=settings.API_V3_STR)
app.include_router(invoicing.router, prefix=settings.API_V3_STR)
app.include_router(sku_mappings.router, prefix=settings.API_V3_STR)
app.include_router(handoffs.router, prefix=settings.API_V3_STR)
app.include_router(operations.router, prefix=settings.API_V3_STR)
app.include_router(order_exceptions.router, prefix=settings.API_V3_STR)
app.include_router(order_templates.router, prefix=settings.API_V3_STR)


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
def readiness_check(db: Session = Depends(get_db, scope="function")):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database readiness check failed",
        ) from exc
    if settings.FILE_STORAGE_PROVIDER == "supabase":
        get_storage().check_private()
    return {"status": "READY", "database": "reachable", "storage": settings.FILE_STORAGE_PROVIDER}
