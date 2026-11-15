"""Plain JSON-ready conversions."""

from datetime import date, datetime
from decimal import Decimal


def json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def product_code(product_id):
    return f"SW-{int(product_id):04d}"


def safe_int(value, default):
    """Query-string integers. A bad value such as ?page=abc falls back instead of a 500."""
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def paginate(items, page, per_page):
    page = max(safe_int(page, 1), 1)
    per_page = min(max(safe_int(per_page, 10), 1), 100)
    total = len(items)
    pages = (total + per_page - 1) // per_page if total else 0
    start = (page - 1) * per_page
    return items[start : start + per_page], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
    }


def permissions_for(role):
    owner = role == "admin"
    return {
        "manage_users": owner,
        "manage_products": owner,
        "manage_suppliers": owner,
        "approve_orders": owner,
        "manage_settings": owner,
        "record_stock": True,
        "run_recommendations": True,
        "view_reports": True,
    }


def user_payload(user, business):
    payload = {
        "user_id": user.user_id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "role_label": "Admin / Owner" if user.role == "admin" else "Staff / Inventory Manager",
        "business_id": business.business_id if business else None,
        "business_name": business.business_name if business else None,
        "business_type": business.business_type if business else None,
        "currency": business.currency if business else "INR",
        "setup_complete": bool(business and business.setup_complete),
        "restock_budget": business.restock_budget if business else 0,
        "permissions": permissions_for(user.role),
    }
    if user.role == "admin" and business is not None:
        payload["join_code"] = business.join_code
    return payload
