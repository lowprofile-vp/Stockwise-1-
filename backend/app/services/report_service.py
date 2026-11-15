"""Report rows shaped for the on-screen table and the CSV download."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.models import Product, PurchaseOrder, PurchaseOrderItem, Supplier, Transaction
from app.services.metrics_service import build_metrics
from app.services.planning import quantize_money, quantize_qty, to_decimal
from app.utils.parsing import as_date, date_end, date_start
from app.utils.serialize import product_code, safe_int


def build_report(kind, business_id, args):
    builders = {
        "inventory": _inventory,
        "low-stock": lambda business, params: _inventory(business, params, statuses={"Low Stock"}),
        "critical": lambda business, params: _inventory(business, params, statuses={"Critical", "Out of Stock"}),
        "sales": _sales,
        "wastage": lambda business, params: _sales(business, params, forced_type="wastage"),
        "suppliers": _suppliers,
        "restocking": _restocking,
        "purchase-orders": _purchase_orders,
        "forecast": _forecast,
    }
    if kind not in builders:
        from app.errors import APIError

        raise APIError("Unknown report.", 404)
    title, columns, rows = builders[kind](business_id, args)
    return {"title": title, "columns": columns, "rows": rows}


def _inventory(business_id, args, statuses=None):
    metrics = build_metrics(business_id)
    products = {product.product_id: product for product in Product.query.filter_by(business_id=business_id).all()}
    category = (args.get("category") or "").strip().lower()
    status = (args.get("status") or "").strip().lower()
    rows = []
    for metric in metrics:
        product = products.get(metric["product_id"])
        if product is None:
            continue
        if category and metric["category"].lower() != category:
            continue
        if statuses and metric["stock_status"] not in statuses:
            continue
        if status and metric["stock_status"].lower() != status:
            continue
        rows.append(
            {
                "code": product_code(product.product_id),
                "name": product.name,
                "category": product.category,
                "current_stock": metric["current_stock"],
                "minimum_stock": product.minimum_stock,
                "unit": product.unit,
                "stock_status": metric["stock_status"],
                "urgency": metric["urgency"],
                "selling_price": product.selling_price,
                "purchase_price": product.purchase_price,
                "stock_value": quantize_money(to_decimal(product.current_stock) * to_decimal(product.purchase_price)),
                "supplier": metric.get("supplier_name") or "",
            }
        )
    title = "Current inventory"
    if statuses == {"Low Stock"}:
        title = "Low-stock report"
    elif statuses == {"Critical", "Out of Stock"}:
        title = "Critical-stock report"
    return title, [
        ("code", "Product ID"),
        ("name", "Product"),
        ("category", "Category"),
        ("current_stock", "Quantity"),
        ("unit", "Unit"),
        ("minimum_stock", "Minimum"),
        ("stock_status", "Status"),
        ("urgency", "Urgency"),
        ("purchase_price", "Purchase price"),
        ("stock_value", "Stock value"),
        ("supplier", "Supplier"),
    ], rows


def _sales(business_id, args, forced_type=None):
    start, end = _range(args)
    query = Transaction.query.filter_by(business_id=business_id).filter(
        Transaction.transaction_date >= date_start(start),
        Transaction.transaction_date <= date_end(end),
    )
    kind = forced_type or (args.get("type") or "").strip()
    if kind:
        query = query.filter_by(transaction_type=kind)
    else:
        query = query.filter(Transaction.transaction_type.in_(["sale", "consumption", "wastage"]))
    if args.get("product_id"):
        query = query.filter_by(product_id=safe_int(args.get("product_id"), 0))
    category = (args.get("category") or "").strip().lower()
    rows = []
    for entry in query.order_by(Transaction.transaction_date.desc()).all():
        product = entry.product
        if product is None:
            continue
        if category and product.category.lower() != category:
            continue
        quantity = abs(to_decimal(entry.quantity))
        price = entry.unit_price if entry.unit_price is not None else Decimal("0")
        rows.append(
            {
                "date": as_date(entry.transaction_date).isoformat(),
                "code": product_code(product.product_id),
                "name": product.name,
                "category": product.category,
                "type": entry.transaction_type,
                "quantity": quantize_qty(quantity),
                "unit": product.unit,
                "unit_price": price,
                "value": quantize_money(quantity * to_decimal(price)) if entry.transaction_type == "sale" else "",
                "reason": entry.reason or "",
                "notes": entry.notes or "",
            }
        )
    title = "Wastage report" if forced_type == "wastage" else "Sales and consumption report"
    return title, [
        ("date", "Date"),
        ("code", "Product ID"),
        ("name", "Product"),
        ("category", "Category"),
        ("type", "Type"),
        ("quantity", "Quantity"),
        ("unit", "Unit"),
        ("unit_price", "Unit price"),
        ("value", "Sale value"),
        ("reason", "Reason"),
    ], rows


def _suppliers(business_id, _args):
    rows = []
    for supplier in Supplier.query.filter_by(business_id=business_id).order_by(Supplier.name).all():
        orders = PurchaseOrder.query.filter_by(business_id=business_id, supplier_id=supplier.supplier_id).all()
        received = [order for order in orders if order.received_at and order.expected_delivery]
        on_time = 0
        delay_total = 0
        for order in received:
            actual = as_date(order.received_at)
            delay = (actual - order.expected_delivery).days
            delay_total += delay
            if delay <= 0:
                on_time += 1
        spend = Decimal("0")
        for order in orders:
            for item in order.items:
                spend += to_decimal(item.received_quantity) * to_decimal(item.unit_cost)
        rows.append(
            {
                "name": supplier.name,
                "contact": supplier.contact_person or "",
                "phone": supplier.phone or "",
                "email": supplier.email or "",
                "delivery_days": supplier.delivery_days,
                "orders": len(orders),
                "on_time_rate": round((on_time / len(received)) * 100, 1) if received else "",
                "average_delay_days": round(delay_total / len(received), 1) if received else "",
                "received_spend": quantize_money(spend),
                "active": "Yes" if supplier.is_active else "No",
            }
        )
    return "Supplier performance report", [
        ("name", "Supplier"),
        ("contact", "Contact"),
        ("phone", "Phone"),
        ("email", "Email"),
        ("delivery_days", "Promised lead time (days)"),
        ("orders", "Orders"),
        ("on_time_rate", "On-time %"),
        ("average_delay_days", "Average delay (days)"),
        ("received_spend", "Received spend"),
        ("active", "Active"),
    ], rows


def _restocking(business_id, args):
    start, end = _range(args, default_days=180)
    rows = []
    orders = PurchaseOrder.query.filter_by(business_id=business_id).filter(
        PurchaseOrder.created_at >= date_start(start),
        PurchaseOrder.created_at <= date_end(end),
    ).all()
    for order in orders:
        for item in order.items:
            received_cost = quantize_money(to_decimal(item.received_quantity) * to_decimal(item.unit_cost))
            rows.append(
                {
                    "code": f"PO-{order.order_id:04d}",
                    "date": as_date(order.created_at).isoformat() if order.created_at else "",
                    "supplier": order.supplier.name if order.supplier else "",
                    "status": order.status,
                    "product": item.product.name if item.product else "",
                    "ordered": item.quantity,
                    "received": item.received_quantity,
                    "unit_cost": item.unit_cost,
                    "received_cost": received_cost,
                }
            )
    return "Restocking expenditure report", [
        ("code", "Order"),
        ("date", "Created"),
        ("supplier", "Supplier"),
        ("status", "Status"),
        ("product", "Product"),
        ("ordered", "Ordered"),
        ("received", "Received"),
        ("unit_cost", "Unit cost"),
        ("received_cost", "Received cost"),
    ], rows


def _purchase_orders(business_id, args):
    start, end = _range(args, default_days=180)
    status = (args.get("status") or "").strip()
    query = PurchaseOrder.query.filter_by(business_id=business_id).filter(
        PurchaseOrder.created_at >= date_start(start),
        PurchaseOrder.created_at <= date_end(end),
    )
    if status:
        query = query.filter_by(status=status)
    rows = []
    for order in query.order_by(PurchaseOrder.created_at.desc()).all():
        rows.append(
            {
                "code": f"PO-{order.order_id:04d}",
                "supplier": order.supplier.name if order.supplier else "",
                "status": order.status,
                "total_cost": order.total_cost,
                "created": as_date(order.created_at).isoformat() if order.created_at else "",
                "expected_delivery": order.expected_delivery.isoformat() if order.expected_delivery else "",
                "received_at": as_date(order.received_at).isoformat() if order.received_at else "",
            }
        )
    return "Purchase order report", [
        ("code", "Order"),
        ("supplier", "Supplier"),
        ("status", "Status"),
        ("total_cost", "Total cost"),
        ("created", "Created"),
        ("expected_delivery", "Expected delivery"),
        ("received_at", "Received"),
    ], rows


def _forecast(business_id, args):
    category = (args.get("category") or "").strip().lower()
    product_id = safe_int(args.get("product_id"), 0) or None
    rows = []
    for metric in build_metrics(business_id):
        if category and metric["category"].lower() != category:
            continue
        if product_id and metric["product_id"] != product_id:
            continue
        rows.append(
            {
                "code": metric["code"],
                "name": metric["name"],
                "category": metric["category"],
                "current_stock": metric["current_stock"],
                "daily_demand": metric["average_daily_demand"],
                "days_until_stockout": metric["days_until_stockout"] if metric["days_until_stockout"] is not None else "",
                "stockout_date": metric["stockout_date"].isoformat() if metric.get("stockout_date") else "",
                "lead_time_days": metric["lead_time_days"],
                "reorder_point": metric["reorder_point"],
                "suggested_quantity": metric["suggested_quantity"],
                "urgency": metric["urgency"],
                "trend": metric["trend"],
                "warning": metric["data_warning"] or "",
            }
        )
    return "Forecast and stockout-risk report", [
        ("code", "Product ID"),
        ("name", "Product"),
        ("category", "Category"),
        ("current_stock", "Stock"),
        ("daily_demand", "Avg daily demand"),
        ("days_until_stockout", "Days to stockout"),
        ("stockout_date", "Stockout date"),
        ("lead_time_days", "Lead time"),
        ("reorder_point", "Reorder point"),
        ("suggested_quantity", "Suggested quantity"),
        ("urgency", "Urgency"),
        ("trend", "Trend"),
        ("warning", "Data warning"),
    ], rows


def _range(args, default_days=30):
    from app.utils.parsing import parse_date

    end = parse_date(args.get("to"), "To") or datetime.now().date()
    start = parse_date(args.get("from"), "From") or (end - timedelta(days=default_days))
    if start > end:
        from app.errors import APIError

        raise APIError("The start date must be on or before the end date.")
    return start, end
