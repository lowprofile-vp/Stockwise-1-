from datetime import datetime

from flask import Blueprint, g, jsonify, request

from app.services.dashboard_service import build_overview, demand_series, inventory_value_trend, notifications_for
from app.services.metrics_service import ENGINE, build_metrics
from app.services.planning import formula_documentation
from app.utils.auth import login_required
from app.utils.parsing import parse_date
from app.utils.serialize import safe_int

bp = Blueprint("analytics", __name__)


@bp.get("/analytics/overview")
@login_required
def overview():
    return jsonify({"data": build_overview(g.business)})


@bp.get("/analytics/demand")
@login_required
def demand():
    payload = demand_series(
        g.business.business_id,
        product_id=request.args.get("product_id"),
        category=request.args.get("category"),
        start=parse_date(request.args.get("from"), "From"),
        end=parse_date(request.args.get("to"), "To"),
    )
    metrics = build_metrics(g.business.business_id)
    if request.args.get("category"):
        metrics = [row for row in metrics if row["category"].lower() == request.args.get("category").lower()]
    if request.args.get("product_id"):
        metrics = [row for row in metrics if row["product_id"] == safe_int(request.args.get("product_id"), 0)]
    payload["increasing"] = [row["name"] for row in metrics if row["trend"] == "increasing"]
    payload["declining"] = [row["name"] for row in metrics if row["trend"] == "declining"]
    payload["inventory_value_trend"] = inventory_value_trend(g.business.business_id, datetime.now().date())
    return jsonify({"data": payload})


@bp.get("/analytics/forecasts")
@login_required
def forecasts():
    rows = build_metrics(g.business.business_id)
    category = (request.args.get("category") or "").strip().lower()
    product_id = request.args.get("product_id")
    if category:
        rows = [row for row in rows if row["category"].lower() == category]
    if product_id:
        rows = [row for row in rows if row["product_id"] == int(product_id)]
    return jsonify({"data": rows, "formula": formula_documentation(), "engine": ENGINE})


@bp.get("/notifications")
@login_required
def notifications():
    return jsonify({"data": notifications_for(g.business)})


@bp.get("/search")
@login_required
def search():
    from app.models import Product, PurchaseOrder, Supplier
    from app.utils.serialize import product_code

    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify({"data": {"products": [], "suppliers": [], "purchase_orders": []}})
    like = f"%{term}%"
    products = (
        Product.query.filter_by(business_id=g.business.business_id)
        .filter(Product.name.ilike(like))
        .limit(8)
        .all()
    )
    suppliers = (
        Supplier.query.filter_by(business_id=g.business.business_id)
        .filter(Supplier.name.ilike(like))
        .limit(6)
        .all()
    )
    orders = []
    if term.upper().startswith("PO-") and term[3:].isdigit():
        orders = PurchaseOrder.query.filter_by(business_id=g.business.business_id, order_id=int(term[3:])).all()
    elif term.isdigit():
        orders = PurchaseOrder.query.filter_by(business_id=g.business.business_id, order_id=int(term)).limit(5).all()
    return jsonify(
        {
            "data": {
                "products": [
                    {"product_id": row.product_id, "code": product_code(row.product_id), "name": row.name, "category": row.category}
                    for row in products
                ],
                "suppliers": [{"supplier_id": row.supplier_id, "name": row.name} for row in suppliers],
                "purchase_orders": [
                    {"order_id": row.order_id, "code": f"PO-{row.order_id:04d}", "status": row.status} for row in orders
                ],
            }
        }
    )
