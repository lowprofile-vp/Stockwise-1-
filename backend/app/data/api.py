"""PostgREST access using the project URL, the publishable key, and the user token.

The publishable key identifies the project. The user's Supabase access token
is the Authorization bearer, so Postgres roles the request as `authenticated`
and Row Level Security applies. No service-role key and no database password
are used.
"""

import json
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import current_app, has_request_context, request
from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, inspect as sa_inspect
from sqlalchemy.sql.elements import BindParameter

from app.errors import APIError

_uow = ContextVar("stockwise_uow", default=None)

_SELECT = {
    "users": "*",
    "businesses": "*",
    "categories": "*",
    "suppliers": "*",
    "products": "*,preferred_supplier:suppliers(*),supplier_links:product_suppliers(*,supplier:suppliers(*))",
    "product_suppliers": "*,product:products(*),supplier:suppliers(*)",
    "transactions": "*,product:products(*),user:users(user_id,name)",
    "purchase_orders": "*,supplier:suppliers(*),creator:users!purchase_orders_created_by_fkey(user_id,name),items:purchase_order_items(*,product:products(*))",
    "purchase_order_items": "*,product:products(*),order:purchase_orders(*,supplier:suppliers(*),creator:users!purchase_orders_created_by_fkey(user_id,name))",
    "restock_recommendations": "*",
    "simulation_runs": "*",
}

_RELATIONS = {
    "products": {"preferred_supplier": "one", "supplier_links": "links"},
    "product_suppliers": {"product": "one", "supplier": "one"},
    "transactions": {"product": "one", "user": "one"},
    "purchase_orders": {"supplier": "one", "creator": "one", "items": "many"},
    "purchase_order_items": {"product": "one", "order": "one"},
}

_RELATION_MODELS = {
    "preferred_supplier": "Supplier",
    "supplier_links": "ProductSupplier",
    "supplier": "Supplier",
    "product": "Product",
    "user": "User",
    "creator": "User",
    "items": "PurchaseOrderItem",
    "order": "PurchaseOrder",
}

# Root table -> foreign table -> (column on the root, primary key of the foreign table)
_JOIN_KEYS = {
    ("purchase_order_items", "purchase_orders"): ("order_id", "order_id"),
    ("product_suppliers", "products"): ("product_id", "product_id"),
}

_EMBED_FOR_SORT = {
    ("purchase_order_items", "purchase_orders"): "order",
    ("product_suppliers", "products"): "product",
}

_OPS = {
    "eq": "eq",
    "ge": "gte",
    "le": "lte",
    "gt": "gt",
    "lt": "lt",
    "ne": "neq",
    "in_op": "in",
    "not_in_op": "not.in",
    "ilike_op": "ilike",
    "like_op": "like",
    "is_": "is_null",
    "is_not": "not_null",
}


def enabled():
    if not has_request_context() and not _has_app():
        return False
    if current_app.config.get("TESTING"):
        return False
    return bool(current_app.config.get("SUPABASE_URL") and current_app.config.get("SUPABASE_PUBLISHABLE_KEY"))


def _has_app():
    try:
        from flask import has_app_context

        return has_app_context()
    except RuntimeError:
        return False


def is_model(entity):
    return isinstance(entity, type) and hasattr(entity, "__tablename__")


def push_uow():
    return _uow.set(UnitOfWork())


def reset_uow(token):
    _uow.reset(token)


def current_uow():
    unit = _uow.get()
    if unit is None:
        unit = UnitOfWork()
        _uow.set(unit)
    return unit


class UnitOfWork:
    def __init__(self):
        self.new = []
        self.deleted = []
        self.tracked = []
        self.identity = {}
        self.managed = []
        self.snapshots = {}

    def clear(self):
        self.new.clear()
        self.deleted.clear()
        self.tracked.clear()
        self.identity.clear()
        self.managed.clear()
        self.snapshots.clear()


class RelatedList(list):
    """Supplier links that can be replaced and appended like the ORM collection."""

    def __init__(self, items=None):
        super().__init__(items or [])
        self.replaced = False
        self.added = []

    def clear(self):
        super().clear()
        self.replaced = True
        self.added = []

    def append(self, item):
        super().append(item)
        self.added.append(item)


class ApiRecord:
    def __init__(self, model, data, relations=None, persisted=True, track=True):
        columns = {column.key: column for column in sa_inspect(model).columns}
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_table", model.__tablename__)
        object.__setattr__(self, "_columns", columns)
        object.__setattr__(self, "_pk", sa_inspect(model).primary_key[0].key)
        object.__setattr__(self, "_data", {})
        object.__setattr__(self, "_rels", relations or {})
        object.__setattr__(self, "_dirty", set())
        object.__setattr__(self, "_persisted", persisted)
        object.__setattr__(self, "_loading", True)
        for key, column in columns.items():
            if key in (data or {}):
                self._data[key] = coerce(column, data[key])
        object.__setattr__(self, "_loading", False)
        if track and persisted:
            _remember(self)

    def __getattr__(self, name):
        data = self._data
        if name in data:
            return data[name]
        rels = self._rels
        if name in rels:
            return rels[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if name in self._columns:
            self._data[name] = value
            if self._persisted and not self._loading:
                self._dirty.add(name)
            return
        if name in self._rels or name in _RELATIONS.get(self._table, {}):
            self._rels[name] = value
            return
        object.__setattr__(self, name, value)


def _remember(record, cache=True):
    unit = current_uow()
    pk = record._data.get(record._pk)
    if cache and pk is not None:
        key = (record._table, pk)
        existing = unit.identity.get(key)
        if existing is not None:
            return existing
        unit.identity[key] = record
    unit.tracked.append(record)
    return record


def track_new(instance):
    current_uow().new.append(instance)


def delete_instance(instance):
    if isinstance(instance, ApiRecord):
        _delete_where(instance._table, [(instance._pk, "eq", instance._data.get(instance._pk))])
        return
    pk = _primary_key(instance)
    value = getattr(instance, pk, None)
    if value is None:
        current_uow().new = [row for row in current_uow().new if row is not instance]
        return
    _delete_where(instance.__tablename__, [(pk, "eq", value)])


def clear_pending():
    unit = _uow.get()
    if unit is not None:
        unit.clear()


def flush_pending():
    unit = current_uow()
    pending = [row for row in unit.new if getattr(row, _primary_key(row), None) is None]
    for row in pending:
        created = _insert_instance(row)
        _apply_returned(row, created)
    unit.new = [row for row in unit.new if getattr(row, _primary_key(row), None) is None]


def commit_pending():
    flush_pending()
    _patch_managed()
    for row in list(current_uow().managed):
        _sync_sa_links(row)
    for record in list(current_uow().tracked):
        _sync_record_links(record)
        changes = {key: record._data.get(key) for key in record._dirty if key != record._pk}
        if changes:
            _patch(record._table, record._pk, record._data.get(record._pk), changes)
            record._dirty.clear()
    for row in list(current_uow().deleted):
        delete_instance(row)
    current_uow().deleted.clear()
    current_uow().new.clear()


def fetch_one(model, ident):
    if ident is None:
        return None
    pk = sa_inspect(model).primary_key[0].key
    cached = current_uow().identity.get((model.__tablename__, ident))
    if cached is not None:
        return cached
    rows = _select(model, [(pk, "eq", ident)], limit=1)
    return rows[0] if rows else None


def provision_profile(name, role, business_name, join_code):
    payload = call_rpc(
        "provision_profile",
        {
            "p_name": name,
            "p_role": role,
            "p_business_name": business_name,
            "p_join_code": join_code,
        },
    )
    if not isinstance(payload, dict) or "user" not in payload or "business" not in payload:
        raise APIError("Supabase did not return the new workspace.", 502)
    from app.models import Business, User

    user = ApiRecord(User, payload["user"], persisted=True, track=False)
    business = ApiRecord(Business, payload["business"], persisted=True, track=False)
    return user, business


def call_rpc(name, args):
    data, _headers = _request("POST", f"/rest/v1/rpc/{name}", body=args, prefer="return=representation")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass
    return data


class ApiQuery:
    def __init__(self, model):
        self.model = model
        self.table = model.__tablename__
        self.filters = []
        self.joins = []
        self.orders = []
        self._limit = None
        self._offset = None

    def filter(self, *criterion):
        for item in criterion:
            self.filters.append(clause_filter(item))
        return self

    def filter_by(self, **kwargs):
        for key, value in kwargs.items():
            self.filters.append(("eq", self.table, key, value))
        return self

    def join(self, target, *args, **kwargs):
        model = target.class_ if hasattr(target, "class_") else target
        if is_model(model):
            self.joins.append(model)
        return self

    def order_by(self, *criterion):
        for item in criterion:
            self.orders.append(order_of(item))
        return self

    def limit(self, count):
        self._limit = count
        return self

    def offset(self, count):
        self._offset = count
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def all(self):
        return self._run()

    def first(self):
        rows = self._run(limit=1 if self._limit is None else self._limit)
        return rows[0] if rows else None

    def one_or_none(self):
        rows = self._run(limit=2)
        if len(rows) > 1:
            raise APIError("More than one matching row was found.", 409)
        return rows[0] if rows else None

    def count(self):
        filters = self._resolved_filters()
        if filters is None:
            return 0
        _params = _filter_params(filters)
        _params.insert(0, ("select", _pk_name(self.model)))
        _data, headers = _request(
            "GET",
            f"/rest/v1/{self.table}",
            params=_params,
            prefer="count=exact",
            range_header="0-0",
        )
        return _content_range_total(headers.get("Content-Range"))

    def delete(self, synchronize_session=None):
        filters = self._resolved_filters()
        if filters is None:
            return 0
        if not filters:
            raise APIError("Refusing to delete every row.", 400)
        return _delete_where(self.table, filters)

    def update(self, values, synchronize_session=None, update_args=None):
        filters = self._resolved_filters()
        if filters is None:
            return 0
        if not filters:
            raise APIError("Refusing to update every row.", 400)
        payload = {}
        for key, value in values.items():
            name = key.key if hasattr(key, "key") else str(key)
            payload[name] = json_ready(value)
        _request("PATCH", f"/rest/v1/{self.table}", params=_filter_params(filters), body=payload, prefer="return=minimal")
        return 1

    def _resolved_filters(self):
        local = []
        grouped = {}
        for kind, table, column, value in self.filters:
            if table == self.table:
                local.append((column, kind, value))
            else:
                grouped.setdefault(table, []).append((column, kind, value))
        for foreign_table, foreign_filters in grouped.items():
            link = _JOIN_KEYS.get((self.table, foreign_table))
            if link is None:
                raise APIError(f"Cannot filter {self.table} through {foreign_table}.", 500)
            local_column, foreign_pk = link
            rows, _headers = _request(
                "GET",
                f"/rest/v1/{foreign_table}",
                params=[("select", foreign_pk), *_filter_params(foreign_filters)],
            )
            ids = [row[foreign_pk] for row in rows or [] if isinstance(row, dict)]
            if not ids:
                return None
            local.append((local_column, "in", ids))
        return local

    def _run(self, limit=None):
        filters = self._resolved_filters()
        if filters is None:
            return []
        foreign_order = [item for item in self.orders if item[0] != self.table]
        params = [("select", _SELECT.get(self.table, "*")), *_filter_params(filters)]
        cap = self._limit if limit is None else limit
        if not foreign_order:
            if self.orders:
                params.append(("order", ",".join(f"{column}.{direction}" for _table, column, direction in self.orders)))
            if cap is not None:
                params.append(("limit", str(cap)))
            if self._offset:
                params.append(("offset", str(self._offset)))
            rows, _headers = _request("GET", f"/rest/v1/{self.table}", params=params)
            return [_record_from_row(self.model, row) for row in rows or []]
        rows, _headers = _request("GET", f"/rest/v1/{self.table}", params=params)
        records = [_record_from_row(self.model, row) for row in rows or []]
        records.sort(key=lambda record: _sort_key(record, self.orders))
        start = self._offset or 0
        end = None if cap is None else start + cap
        return records[start:end]


def clause_filter(expression):
    name = getattr(expression.operator, "__name__", "")
    kind = _OPS.get(name)
    if kind is None:
        raise APIError("That filter is not available through the Supabase Data API.", 500)
    column = expression.left
    table = column.table.name
    key = column.key
    if kind in {"is_null", "not_null"}:
        return kind, table, key, None
    value = _bound_value(expression.right)
    if kind in {"ilike", "like"} and isinstance(value, str):
        value = value.replace("%", "*")
    return kind, table, key, value


def order_of(expression):
    modifier = getattr(expression, "modifier", None)
    modifier_name = getattr(modifier, "__name__", "")
    if modifier_name:
        column = expression.element
        direction = "desc" if "desc" in modifier_name else "asc"
    else:
        column = expression
        direction = "asc"
    return column.table.name, column.key, direction


def param_for(column, kind, value):
    if kind == "is_null":
        return column, "is.null"
    if kind == "not_null":
        return column, "not.is.null"
    if kind in {"in", "not.in"}:
        rendered = ",".join(_in_item(item) for item in value)
        return column, f"{kind}.({rendered})"
    # eq."uuid" is compared as the four extra quote characters, so scalar filters stay bare.
    return column, f"{kind}.{_scalar(value)}"


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _as_text(value)


def _in_item(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    text = _as_text(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + text + '"'


def _as_text(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _filter_params(filters):
    params = []
    for column, kind, value in filters:
        key, rendered = param_for(column, kind, value)
        params.append((key, rendered))
    return params


def _bound_value(element):
    if isinstance(element, BindParameter) or hasattr(element, "effective_value"):
        return element.effective_value
    return getattr(element, "value", element)


def _pk_name(model):
    return sa_inspect(model).primary_key[0].key


def _primary_key(instance):
    return sa_inspect(type(instance)).primary_key[0].key


def _sort_key(record, orders):
    values = []
    for table, column, direction in orders:
        if table == record._table:
            value = record._data.get(column)
        else:
            related = record._rels.get(_EMBED_FOR_SORT.get((record._table, table), ""))
            value = None if related is None else getattr(related, column, None)
        values.append((value is None, value if direction == "asc" else _reverse(value)))
    return values


def _reverse(value):
    if isinstance(value, (int, float, Decimal)):
        return -value
    if isinstance(value, datetime):
        return -value.timestamp()
    if isinstance(value, date):
        return -value.toordinal()
    if isinstance(value, str):
        return tuple(-ord(char) for char in value)
    return value


def _select(model, filters, limit=None):
    params = [("select", _SELECT.get(model.__tablename__, "*")), *_filter_params(filters)]
    if limit is not None:
        params.append(("limit", str(limit)))
    rows, _headers = _request("GET", f"/rest/v1/{model.__tablename__}", params=params)
    return [_record_from_row(model, row) for row in rows or []]


def _record_from_row(model, row):
    if not isinstance(row, dict):
        raise APIError("Supabase returned an unexpected row.", 502)
    relations = {}
    for name, kind in _RELATIONS.get(model.__tablename__, {}).items():
        if name not in row:
            continue
        relations[name] = _relation(name, kind, row.get(name))
    record = ApiRecord(model, row, relations=relations, persisted=True, track=True)
    cached = current_uow().identity.get((record._table, record._data.get(record._pk)))
    return cached if cached is not None else record


def _relation(name, kind, value):
    model = _model_by_name(_RELATION_MODELS[name])
    if kind == "one":
        if not isinstance(value, dict):
            return None
        return ApiRecord(model, value, relations=_nested_relations(model, value), persisted=True, track=False)
    items = []
    for item in value or []:
        if isinstance(item, dict):
            items.append(ApiRecord(model, item, relations=_nested_relations(model, item), persisted=True, track=True))
    if kind == "links":
        return RelatedList(items)
    return items


def _nested_relations(model, row):
    relations = {}
    for name, kind in _RELATIONS.get(model.__tablename__, {}).items():
        if name in row and kind == "one":
            relations[name] = _relation(name, kind, row.get(name))
    return relations


def _model_by_name(name):
    from app import models

    return getattr(models, name)


def _insert_instance(instance):
    payload = {}
    for column in sa_inspect(type(instance)).columns:
        if column.primary_key:
            continue
        value = getattr(instance, column.key)
        if value is None:
            continue
        payload[column.key] = json_ready(value)
    rows, _headers = _request(
        "POST",
        f"/rest/v1/{instance.__tablename__}",
        body=payload,
        prefer="return=representation",
    )
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows or {}


def _apply_returned(instance, data):
    if not isinstance(data, dict):
        return
    for column in sa_inspect(type(instance)).columns:
        if column.key in data:
            setattr(instance, column.key, coerce(column, data[column.key]))
    unit = current_uow()
    if id(instance) not in unit.snapshots:
        unit.managed.append(instance)
    unit.snapshots[id(instance)] = _column_values(instance)


def _column_values(instance):
    return {column.key: getattr(instance, column.key) for column in sa_inspect(type(instance)).columns}


def _patch_managed():
    unit = current_uow()
    for instance in unit.managed:
        before = unit.snapshots.get(id(instance), {})
        pk = _primary_key(instance)
        changes = {}
        for column in sa_inspect(type(instance)).columns:
            if column.primary_key:
                continue
            current = getattr(instance, column.key)
            if current != before.get(column.key):
                changes[column.key] = current
        if changes:
            _patch(instance.__tablename__, pk, getattr(instance, pk), changes)
            unit.snapshots[id(instance)] = _column_values(instance)


def _patch(table, pk_name, pk_value, changes):
    if pk_value is None or not changes:
        return
    payload = {key: json_ready(value) for key, value in changes.items()}
    _request(
        "PATCH",
        f"/rest/v1/{table}",
        params=_filter_params([(pk_name, "eq", pk_value)]),
        body=payload,
        prefer="return=minimal",
    )


def _delete_where(table, filters):
    _data, headers = _request(
        "DELETE",
        f"/rest/v1/{table}",
        params=_filter_params(filters),
        prefer="count=exact",
    )
    return _content_range_total(headers.get("Content-Range"))


def _sync_record_links(record):
    links = record._rels.get("supplier_links")
    if not isinstance(links, RelatedList):
        return
    if links.replaced:
        _delete_where("product_suppliers", [("product_id", "eq", record.product_id)])
        links.replaced = False
    for item in list(links.added):
        if getattr(item, "product_id", None) in (None, ""):
            if isinstance(item, ApiRecord):
                item.product_id = record.product_id
            else:
                item.product_id = record.product_id
        if isinstance(item, ApiRecord):
            if item._data.get("id") is None:
                created = _insert_instance_from_mapping(
                    "product_suppliers",
                    {
                        "product_id": item.product_id,
                        "supplier_id": item.supplier_id,
                        "purchase_price": item.purchase_price,
                        "minimum_order_quantity": item.minimum_order_quantity,
                    },
                )
                if created and created.get("id") is not None:
                    item.id = created["id"]
        else:
            created = _insert_instance(item)
            _apply_returned(item, created)
    links.added.clear()


def _sync_sa_links(instance):
    from app.models import Product

    if not isinstance(instance, Product):
        return
    state = sa_inspect(instance, raiseerr=False)
    if state is None or "supplier_links" not in state.dict:
        return
    for link in list(instance.supplier_links):
        if getattr(link, "id", None):
            continue
        if getattr(link, "product_id", None) is None:
            link.product_id = instance.product_id
        created = _insert_instance(link)
        _apply_returned(link, created)


def _insert_instance_from_mapping(table, payload):
    rows, _headers = _request(
        "POST",
        f"/rest/v1/{table}",
        body={key: json_ready(value) for key, value in payload.items() if value is not None},
        prefer="return=representation",
    )
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows or {}


def coerce(column, value):
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, Boolean):
        return bool(value)
    if isinstance(kind, Integer):
        return int(value)
    if isinstance(kind, Numeric):
        return Decimal(str(value))
    if isinstance(kind, DateTime):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if isinstance(kind, Date) and not isinstance(kind, DateTime):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])
    return value


def json_ready(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _content_range_total(header):
    if not header or "/" not in header:
        return 0
    total = header.rsplit("/", 1)[1]
    if total == "*":
        return 0
    try:
        return int(total)
    except ValueError:
        return 0


def _bearer():
    if not has_request_context():
        raise APIError("Login required.", 401)
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise APIError("Login required.", 401)
    token = header.split(" ", 1)[1].strip()
    if not token:
        raise APIError("Login required.", 401)
    return token


_NAME_SAFE = "*._"
_VALUE_SAFE = '",()*._-:'


def query_string(params):
    # Leave PostgREST punctuation intact, but encode "+" so "+00:00" is not read as a space.
    # The safe sets live in constants: a backslash inside an f-string expression is a
    # SyntaxError before Python 3.12, which crashed the API on hosts running 3.11.
    return "&".join(
        quote(str(name), safe=_NAME_SAFE) + "=" + quote(str(value), safe=_VALUE_SAFE)
        for name, value in params
    )


def _request(method, path, *, params=None, body=None, prefer=None, range_header=None):
    base = current_app.config["SUPABASE_URL"].rstrip("/")
    key = current_app.config["SUPABASE_PUBLISHABLE_KEY"]
    if not base or not key:
        raise APIError("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.", 500)
    url = base + path
    if params:
        url += "?" + query_string(params)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    outgoing = Request(url, data=payload, method=method)
    outgoing.add_header("apikey", key)
    outgoing.add_header("Authorization", "Bearer " + _bearer())
    outgoing.add_header("Accept", "application/json")
    if payload is not None:
        outgoing.add_header("Content-Type", "application/json")
    if prefer:
        outgoing.add_header("Prefer", prefer)
    if range_header:
        outgoing.add_header("Range-Unit", "items")
        outgoing.add_header("Range", range_header)
    try:
        with urlopen(outgoing, timeout=20) as response:
            raw = response.read()
            headers = {name: value for name, value in response.headers.items()}
            if not raw:
                return None, headers
            return json.loads(raw.decode("utf-8")), headers
    except HTTPError as exc:
        if exc.code == 416:
            return None, {"Content-Range": "*/0"}
        raw = exc.read().decode("utf-8", errors="replace")
        raise APIError(_error_message(exc.code, raw), _error_status(exc.code)) from exc
    except URLError as exc:
        raise APIError("Could not reach Supabase.", 502) from exc


def _error_status(code):
    if code in {401, 403}:
        return 403
    if 400 <= code < 500:
        return code
    return 502


def _error_message(code, raw):
    message = raw
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        message = parsed.get("message") or parsed.get("msg") or parsed.get("error") or parsed.get("hint") or raw
        hint = parsed.get("code") or ""
    else:
        hint = ""
    text = str(message or "Supabase rejected the request.")
    missing = hint in {"PGRST205", "42883", "42P01"} or "schema cache" in text.lower() or "could not find the function" in text.lower()
    if missing:
        return "The Supabase tables are not ready. Run backend/supabase_schema.sql in the Supabase SQL editor, then try again."
    if "row-level security" in text.lower() or code in {401, 403}:
        return "You do not have access to that."
    if "duplicate key" in text.lower() or hint == "23505":
        return "That record already exists."
    return text
