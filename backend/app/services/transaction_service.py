"""Sales, consumption, wastage, and CSV import.

Imported rows are validated first. The valid rows then pass through
OperationQueue so stock updates run in file order inside one database
transaction.
"""

import csv
import io
from collections import defaultdict
from datetime import timedelta

from app.algorithms.queue import OperationQueue
from app.errors import APIError
from app.models import Product, Transaction
from app.services.planning import quantize_money, quantize_qty, to_decimal
from app.services.stock_service import apply_stock_change, movement_payload
from app.utils.parsing import as_date, date_end, date_start, future_limit, parse_datetime
from app.utils.serialize import safe_int


ALLOWED = {"sale", "consumption", "wastage", "adjustment"}


def record_transaction(user, payload):
    operations = [_normalize(payload, user, row=None)]
    return process_operations(operations, user)[0]


def process_operations(operations, user):
    queue = OperationQueue()
    for operation in operations:
        queue.enqueue(operation)
    results = []
    while not queue.is_empty():
        operation = queue.dequeue()
        try:
            results.append(_apply(operation, user))
        except APIError as exc:
            if operation.get("row"):
                raise APIError(f"Row {operation['row']}: {exc.message}", exc.status) from exc
            raise
    return results


def list_transactions(business_id, args):
    query = Transaction.query.filter_by(business_id=business_id)
    product_id = args.get("product_id")
    if product_id:
        query = query.filter_by(product_id=int(product_id))
    kind = (args.get("type") or args.get("transaction_type") or "").strip()
    if kind:
        query = query.filter_by(transaction_type=kind)
    if args.get("from"):
        query = query.filter(Transaction.transaction_date >= date_start(as_date_arg(args.get("from"))))
    if args.get("to"):
        query = query.filter(Transaction.transaction_date <= date_end(as_date_arg(args.get("to"))))
    total = query.count()
    page = max(safe_int(args.get("page"), 1), 1)
    per_page = min(max(safe_int(args.get("per_page"), 15), 1), 100)
    rows = (
        query.order_by(Transaction.transaction_date.desc(), Transaction.transaction_id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return [movement_payload(row) for row in rows], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page if total else 0,
    }


def summarize(business_id, args):
    from datetime import date as date_cls

    end = as_date_arg(args.get("to")) if args.get("to") else date_cls.today()
    start = as_date_arg(args.get("from")) if args.get("from") else end - timedelta(days=29)
    rows = (
        Transaction.query.filter_by(business_id=business_id)
        .filter(Transaction.transaction_date >= date_start(start))
        .filter(Transaction.transaction_date <= date_end(end))
        .all()
    )
    totals = defaultdict(lambda: {"count": 0, "quantity": to_decimal(0), "value": to_decimal(0)})
    daily = defaultdict(lambda: to_decimal(0))
    for entry in rows:
        bucket = totals[entry.transaction_type]
        bucket["count"] += 1
        quantity = abs(to_decimal(entry.quantity))
        bucket["quantity"] += quantity
        price = entry.unit_price
        if price is None and entry.product is not None and entry.transaction_type == "sale":
            price = entry.product.selling_price
        if entry.transaction_type == "sale" and price is not None:
            value = quantity * to_decimal(price)
            bucket["value"] += value
            daily[as_date(entry.transaction_date).isoformat()] += value
    day_rows = [{"date": day, "revenue": daily[day]} for day in sorted(daily)]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "by_type": {
            key: {"count": value["count"], "quantity": quantize_qty(value["quantity"]), "value": quantize_money(value["value"])}
            for key, value in totals.items()
        },
        "sales_revenue": quantize_money(totals["sale"]["value"]),
        "daily_revenue": day_rows,
    }


def import_csv(file_storage, user):
    if file_storage is None:
        raise APIError("Choose a CSV file to import.")
    raw = file_storage.read()
    if not raw:
        raise APIError("The CSV file is empty.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise APIError("The CSV file must be UTF-8 text.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise APIError("The CSV file needs a header row.")
    errors = []
    operations = []
    for line_no, row in enumerate(reader, start=2):
        if not row or not any((value or "").strip() for value in row.values() if value is not None):
            continue
        try:
            operations.append(_normalize(_csv_payload(row, user), user, row=line_no))
        except APIError as exc:
            errors.append({"row": line_no, "error": exc.message})
    if errors:
        raise APIError("The file has invalid rows. No stock was changed.", details={"rows": errors})
    if not operations:
        raise APIError("The file has no data rows.")
    results = process_operations(operations, user)
    return {
        "imported": len(results),
        "processing": "Rows were applied in file order through OperationQueue.",
        "transactions": [movement_payload(result) for result in results],
    }


def csv_template():
    return (
        "product_id,product_name,transaction_type,quantity,unit_price,transaction_date,reason,notes\n"
        "1,,sale,2,60,2026-10-01,Counter sale,\n"
    )


def _apply(operation, user):
    product = Product.query.filter_by(product_id=operation["product_id"], business_id=user.business_id).first()
    if product is None:
        raise APIError("Product not found.", 404)
    return apply_stock_change(
        product=product,
        user=user,
        transaction_type=operation["transaction_type"],
        quantity=operation["quantity"],
        unit_price=operation.get("unit_price"),
        reason=operation.get("reason"),
        notes=operation.get("notes"),
        transaction_date=operation.get("transaction_date"),
    )


def _normalize(payload, user, row):
    kind = str(payload.get("transaction_type") or payload.get("type") or "").strip().lower()
    if kind not in ALLOWED:
        raise APIError("Transaction type must be sale, consumption, wastage, or adjustment.")
    product = _find_product(user.business_id, payload)
    quantity = quantize_qty(payload.get("quantity"))
    if kind != "adjustment" and quantity <= 0:
        raise APIError("Quantity must be greater than zero.")
    if kind == "adjustment" and quantity == 0:
        raise APIError("Adjustment quantity must not be zero.")
    if kind == "wastage" and not str(payload.get("reason") or "").strip():
        raise APIError("A reason is required for wastage.")
    if kind == "adjustment" and not str(payload.get("reason") or "").strip():
        raise APIError("A reason is required for a stock adjustment.")
    moment = future_limit(parse_datetime(payload.get("transaction_date"), "Transaction date"), "Transaction date")
    unit_price = payload.get("unit_price")
    if kind == "sale":
        unit_price = product.selling_price if unit_price in (None, "") else quantize_money(unit_price)
    elif unit_price not in (None, ""):
        unit_price = quantize_money(unit_price)
    else:
        unit_price = None
    reason = (payload.get("reason") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    if reason and len(reason) > 200:
        raise APIError("Reason must be at most 200 characters.")
    if notes and len(notes) > 500:
        raise APIError("Notes must be at most 500 characters.")
    return {
        "row": row,
        "product_id": product.product_id,
        "transaction_type": kind,
        "quantity": quantity,
        "unit_price": unit_price,
        "reason": reason,
        "notes": notes,
        "transaction_date": moment,
    }


def _find_product(business_id, payload):
    product_id = payload.get("product_id")
    name = (payload.get("product_name") or payload.get("name") or "").strip()
    product = None
    if product_id not in (None, ""):
        product = Product.query.filter_by(business_id=business_id, product_id=int(product_id)).first()
    elif name:
        matches = Product.query.filter(Product.business_id == business_id, Product.name.ilike(name)).all()
        if len(matches) > 1:
            raise APIError(f"More than one product is named '{name}'. Use the product id.")
        product = matches[0] if matches else None
    else:
        raise APIError("A product id or product name is required.")
    if product is None:
        raise APIError("Product not found.", 404)
    return product


def _csv_payload(row, user):
    def cell(*names):
        for name in names:
            if name in row and row[name] is not None and str(row[name]).strip() != "":
                return str(row[name]).strip()
        return None

    lowered = {((key or "").strip().lower()): value for key, value in row.items()}

    def pick(*names):
        for name in names:
            if name in lowered and lowered[name] is not None and str(lowered[name]).strip() != "":
                return str(lowered[name]).strip()
        return None

    return {
        "product_id": pick("product_id", "id"),
        "product_name": pick("product_name", "name", "product"),
        "transaction_type": pick("transaction_type", "type"),
        "quantity": pick("quantity", "qty"),
        "unit_price": pick("unit_price", "price"),
        "transaction_date": pick("transaction_date", "date"),
        "reason": pick("reason"),
        "notes": pick("notes", "note"),
    }


def as_date_arg(value):
    from app.utils.parsing import parse_date

    parsed = parse_date(value, "Date", required=True)
    return parsed
