"""
app/api/dashboard.py  –  /api/v1/dashboard
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Depends

from app.modules.dashboard_analytics import DashboardAnalyticsService
from app.utils.logger import get_logger
from app.utils.security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = get_logger(__name__)
_svc = DashboardAnalyticsService()


@router.get("/overview", summary="Overview KPIs")
def get_overview(user: dict = Depends(get_current_user)):
    """Return high-level KPI metrics for the dashboard header.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the metrics to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <overview metrics>}`` where
            ``data`` is produced by
            ``DashboardAnalyticsService.get_overview_metrics``.

    Raises:
        HTTPException: 500 if the metrics cannot be computed.
    """
    try:
        return {"status": "ok", "data": _svc.get_overview_metrics(merchant_id=user["merchant_id"])}
    except Exception as exc:
        logger.error("overview error", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/customers", summary="Customer insights")
def get_customer_insights(user: dict = Depends(get_current_user)):
    """Return demographics and behaviour analytics for the merchant's patients.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the insights to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <customer insights>}`` where
            ``data`` is produced by
            ``DashboardAnalyticsService.get_customer_insights``.

    Raises:
        HTTPException: 500 if the insights cannot be computed.
    """
    try:
        return {"status": "ok", "data": _svc.get_customer_insights(merchant_id=user["merchant_id"])}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/products", summary="Product analytics")
def get_product_analytics(user: dict = Depends(get_current_user)):
    """Return top medicines, category breakdown, and inventory health.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the analytics to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <product analytics>}`` where
            ``data`` is produced by
            ``DashboardAnalyticsService.get_product_analytics``.

    Raises:
        HTTPException: 500 if the analytics cannot be computed.
    """
    try:
        return {"status": "ok", "data": _svc.get_product_analytics(merchant_id=user["merchant_id"])}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/orders", summary="Order analytics")
def get_order_analytics(user: dict = Depends(get_current_user)):
    """Return order status breakdown, payment methods, and average order value.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the analytics to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <order analytics>}`` where ``data``
            is produced by ``DashboardAnalyticsService.get_order_analytics``.

    Raises:
        HTTPException: 500 if the analytics cannot be computed.
    """
    try:
        return {"status": "ok", "data": _svc.get_order_analytics(merchant_id=user["merchant_id"])}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/timeseries", summary="Time-series data")
def get_timeseries(
    metric: str = Query(default="orders", regex="^(orders|revenue)$"),
    period: str = Query(default="30d", regex="^(7d|30d|90d|365d)$"),
    user: dict = Depends(get_current_user),
):
    """Return a daily time-series for orders or revenue.

    Args:
        metric: The metric to chart, either ``"orders"`` or ``"revenue"``.
        period: The lookback window, one of ``"7d"``, ``"30d"``, ``"90d"``,
            or ``"365d"``.
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the data to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "metric": ..., "period": ..., "data": [...]}``
            with the daily time-series points from
            ``DashboardAnalyticsService.get_timeseries_data``.

    Raises:
        HTTPException: 422 if ``metric`` or ``period`` fail the query regex
            validation; 500 if the data cannot be computed.
    """
    try:
        data = _svc.get_timeseries_data(metric=metric, period=period, merchant_id=user["merchant_id"])
        return {"status": "ok", "metric": metric, "period": period, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/refresh-cache", summary="Force-refresh dashboard cache")
def refresh_cache(user: dict = Depends(get_current_user)):
    """Invalidate and pre-warm the in-memory analytics cache.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the cache refresh to the caller's
            pharmacy.

    Returns:
        dict: The result of
            ``DashboardAnalyticsService.refresh_dashboard_cache``,
            confirming the cache was invalidated and rebuilt.

    Raises:
        HTTPException: 500 if the cache refresh fails.
    """
    try:
        return _svc.refresh_dashboard_cache(merchant_id=user["merchant_id"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/ops-status", summary="Operational status for dashboard production telemetry")
def get_operational_status(user: dict = Depends(get_current_user)):
    """Return data freshness and agent execution telemetry for the tenant.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the telemetry to the caller's
            pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <operational status>}`` where
            ``data`` is produced by
            ``DashboardAnalyticsService.get_operational_status``.

    Raises:
        HTTPException: 500 if the operational status cannot be computed.
    """
    try:
        return {
            "status": "ok",
            "data": _svc.get_operational_status(merchant_id=user["merchant_id"]),
        }
    except Exception as exc:
        logger.error("ops status error", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))