"""Fail-closed DuckDB 2 SELECT subset for trusted host-configured readers."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


class SqlPolicyError(ValueError):
    def __init__(self, code="SQL_NOT_ALLOWED"):
        self.code = code
        super().__init__(code)


def _reject():
    raise SqlPolicyError()


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
        _reject()
    return value.lower()


# Platform registry: host policy cannot extend functions. Include bounded wrapper
# helpers and parser/metadata helpers so the guard also protects its own bindings.
FUNCTIONS = frozenset("""count count_star sum avg min max lower upper length substr substring
coalesce nullif abs round ceil floor greatest least trim ltrim rtrim concat concat_ws
replace starts_with ends_with contains json_extract json_extract_string json_array_length
json_type json_valid regexp_extract regexp_matches row_number rank dense_rank lag lead
first_value last_value nth_value ntile cume_dist percent_rank date_part date_trunc
""".split())
OPERATORS = frozenset({"+", "-", "*", "/", "//", "%", "||", "->", "->>", "~~", "!~~", "~~*", "!~~*"})
TRUSTED_HELPERS = frozenset({"json_serialize_sql", "duckdb_tables", "duckdb_views", "duckdb_functions", "duckdb_columns",
                             "current_database", "current_schema", "current_setting", "current_snapshot", "ducklake_current_snapshot"})
GUARDED_FUNCTIONS = FUNCTIONS | OPERATORS | TRUSTED_HELPERS
RESERVED = {"system", "temp", "main", "information_schema", "pg_catalog"}


@dataclass(frozen=True)
class QueryPolicy:
    relations: frozenset[tuple[str, str]]
    version: int = 1


@dataclass(frozen=True)
class ValidatedSelect:
    relations: frozenset[tuple[str, str]]
    functions: frozenset[str]


def load_query_policy():
    try:
        home = os.environ.get("OLAV_HOME")
        if not home:
            raise ValueError()
        path = Path(home) / ".olav/config/query-policy.json"
        with path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        obj = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(obj, dict) or set(obj) != {"version", "relations"} or type(obj["version"]) is not int or obj["version"] != 1:
            raise ValueError()
        if not isinstance(obj["relations"], list) or not 1 <= len(obj["relations"]) <= 64:
            raise ValueError()
        relations = set()
        for item in obj["relations"]:
            if not isinstance(item, dict) or set(item) != {"schema", "table"}:
                raise ValueError()
            schema, table = _name(item["schema"]), _name(item["table"])
            if schema in RESERVED or (schema, table) in relations:
                raise ValueError()
            relations.add((schema, table))
        return QueryPolicy(frozenset(relations))
    except Exception:
        raise SqlPolicyError("POLICY_UNRESOLVED") from None


def _fields(obj, allowed):
    if not isinstance(obj, dict):
        _reject()
    text_fields = {"class", "type", "alias", "schema_name", "table_name", "catalog_name", "relation_name",
                   "function_name", "schema", "catalog", "kind", "text", "identifier", "name",
                   "join_type", "ref_type", "nearest_order_type", "aggregate_handling", "materialized",
                   "key", "start", "end", "exclude_clause", "null_order", "limit_type", "type_name"}
    int_fields = {"query_location", "query_location_length", "nearest_count"}
    bool_fields = {"error", "distinct", "is_operator", "export_state", "columns", "ignore_nulls",
                   "has_ignore_nulls", "delim_flipped", "is_implicit", "nearest_approx", "try_cast"}
    names_fields = {"column_names", "column_name_alias", "aliases", "using_columns", "path"}
    list_fields = {"statements", "map", "select_list", "group_expressions", "group_sets", "modifiers",
                   "arguments", "partitions", "orders", "arg_orders", "children", "case_checks",
                   "distinct_on_targets", "named_param_map", "key_targets", "payload_aggregates",
                   "exclude_list", "replace_list", "qualified_exclude_list", "rename_list", "duplicate_eliminated_columns"}
    for key, value in obj.items():
        if key in allowed:
            if key in text_fields and not isinstance(value, str):
                _reject()
            if key in int_fields and type(value) is not int:
                _reject()
            if key in bool_fields and type(value) is not bool:
                _reject()
            if key in names_fields and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
                _reject()
            if key in list_fields and not isinstance(value, list):
                _reject()
    # Future serializer fields may appear only with their empty/default value.
    for key, value in obj.items():
        if key not in allowed and value not in (None, False, "", [], {}):
            _reject()


_EXPR_COMMON = {"class", "type", "alias", "query_location", "query_location_length"}
_TABLE_COMMON = {"type", "alias", "sample", "query_location", "query_location_length"}


def validate_select(conn, sql, params, policy):
    """Parse only, before any user binding. Never execute deserialized SQL."""
    if not isinstance(sql, str) or not 0 < len(sql.encode("utf-8")) <= 16384:
        _reject()
    if params is not None:
        if not isinstance(params, (list, tuple)) or len(params) > 256:
            _reject()
        for value in params:
            if type(value) not in (str, int, float, bool, type(None)):
                _reject()
        if len(json.dumps(params, allow_nan=False).encode("utf-8")) > 16384:
            _reject()
    try:
        doc = json.loads(conn.execute("SELECT system.main.json_serialize_sql(?)", [sql]).fetchone()[0])
        _fields(doc, {"error", "statements"})
        if doc.get("error") is not False or len(doc.get("statements", [])) != 1:
            _reject()
        statement = doc["statements"][0]
        _fields(statement, {"node", "named_param_map"})
        def parameter_map(items):
            if not isinstance(items, list):
                _reject()
            for item in items:
                _fields(item, {"key", "value"})
                if not item.get("key", "").isdigit() or type(item.get("value")) is not int or not 1 <= item["value"] <= 256:
                    _reject()
        parameter_map(statement.get("named_param_map", []))
        refs, funcs = set(), set()

        def expression(e, scope):
            if e is None:
                return
            if not isinstance(e, dict):
                _reject()
            cls, kind = e.get("class"), e.get("type")
            def fields(extra):
                _fields(e, _EXPR_COMMON | set(extra))
            if cls == "COLUMN_REF" and kind == "COLUMN_REF":
                fields({"column_names"})
                if not isinstance(e.get("column_names"), list) or not 1 <= len(e["column_names"]) <= 3:
                    _reject()
            elif cls == "STAR" and kind == "STAR":
                fields({"relation_name", "exclude_list", "replace_list", "columns", "expr", "qualified_exclude_list", "rename_list"})
                if any(e.get(k) for k in ("exclude_list", "replace_list", "columns", "expr", "qualified_exclude_list", "rename_list")):
                    _reject()
            elif cls == "CONSTANT" and kind == "VALUE_CONSTANT":
                fields({"literal"})
                literal = e.get("literal")
                if literal is None:
                    _reject()
                if literal is not None:
                    _fields(literal, {"kind", "text"})
                    if literal.get("kind") not in {"STRING", "INTEGER", "DECIMAL", "DOUBLE", "NULL", "NULL_LITERAL", "NUMERIC", "BOOLEAN"}:
                        _reject()
            elif cls == "CAST" and kind == "OPERATOR_CAST":
                fields({"child", "try_cast", "type_expr"})
                typ = e["type_expr"]
                _fields(typ, _EXPR_COMMON | {"catalog", "schema", "type_name", "children", "qualified_name"})
                if typ.get("class") != "TYPE" or typ.get("type") != "TYPE" or typ.get("catalog") or typ.get("schema") or typ.get("children"):
                    _reject()
                if str(typ.get("type_name", "")).upper() not in {"VARCHAR", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "FLOAT", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMPTZ", "JSON"}:
                    _reject()
                qualified(typ.get("qualified_name"), [typ["type_name"]])
                expression(e["child"], scope)
            elif cls == "PARAMETER" and kind == "VALUE_PARAMETER":
                fields({"identifier"})
                if not str(e.get("identifier", "")).isdigit():
                    _reject()
            elif cls == "FUNCTION" and kind == "FUNCTION":
                fields({"function_name", "schema", "catalog", "filter", "order_bys", "distinct", "is_operator", "export_state", "arguments", "qualified_name"})
                name = e.get("function_name")
                canonical = name.lower() if isinstance(name, str) and name.isascii() else ""
                if canonical not in FUNCTIONS | OPERATORS or e.get("schema") or e.get("catalog") or e.get("export_state"):
                    _reject()
                qualified(e.get("qualified_name"), [name])
                funcs.add(canonical)
                for arg in e.get("arguments", []):
                    _fields(arg, {"name", "expression"})
                    if arg.get("name"):
                        _reject()
                    expression(arg["expression"], scope)
                expression(e.get("filter"), scope)
                order_modifier(e.get("order_bys"), scope)
            elif cls == "WINDOW" and kind in {"WINDOW_ROW_NUMBER", "WINDOW_RANK", "WINDOW_RANK_DENSE", "WINDOW_AGGREGATE", "WINDOW_LEAD", "WINDOW_LAG", "WINDOW_FIRST_VALUE", "WINDOW_LAST_VALUE", "WINDOW_NTILE", "WINDOW_PERCENT_RANK", "WINDOW_CUME_DIST", "WINDOW_NTH_VALUE"}:
                fields({"function_name", "schema", "catalog", "partitions", "orders", "start", "end", "start_expr", "end_expr", "offset_expr", "default_expr", "ignore_nulls", "filter_expr", "exclude_clause", "distinct", "arg_orders", "has_ignore_nulls", "arguments", "qualified_name"})
                name = _name(e.get("function_name"))
                if name not in FUNCTIONS or e.get("schema") or e.get("catalog") or e.get("exclude_clause") not in (None, "NO_OTHER"):
                    _reject()
                qualified(e.get("qualified_name"), [e["function_name"]])
                funcs.add(name)
                if e.get("start") not in {"UNBOUNDED_PRECEDING", "CURRENT_ROW_RANGE", "CURRENT_ROW_ROWS", "EXPR_PRECEDING_ROWS", "EXPR_FOLLOWING_ROWS"} or e.get("end") not in {"CURRENT_ROW_RANGE", "CURRENT_ROW_ROWS", "UNBOUNDED_FOLLOWING", "EXPR_PRECEDING_ROWS", "EXPR_FOLLOWING_ROWS"}:
                    _reject()
                for item in e.get("partitions", []) + e.get("arguments", []):
                    expression(item, scope)
                orders(e.get("orders", []), scope)
                orders(e.get("arg_orders", []), scope)
                for key in ("start_expr", "end_expr", "offset_expr", "default_expr", "filter_expr"):
                    expression(e.get(key), scope)
            elif cls == "COMPARISON" and kind in {"COMPARE_EQUAL", "COMPARE_NOTEQUAL", "COMPARE_LESSTHAN", "COMPARE_GREATERTHAN", "COMPARE_LESSTHANOREQUALTO", "COMPARE_GREATERTHANOREQUALTO", "COMPARE_DISTINCT_FROM", "COMPARE_NOT_DISTINCT_FROM"}:
                fields({"left", "right"})
                expression(e["left"], scope); expression(e["right"], scope)
            elif cls == "CONJUNCTION" and kind in {"CONJUNCTION_AND", "CONJUNCTION_OR"}:
                fields({"children"})
                for child in e["children"]:
                    expression(child, scope)
            elif cls == "OPERATOR" and kind in {"OPERATOR_NOT", "OPERATOR_IS_NULL", "OPERATOR_IS_NOT_NULL", "COMPARE_IN", "COMPARE_NOT_IN", "OPERATOR_COALESCE"}:
                fields({"children"})
                for child in e["children"]:
                    expression(child, scope)
            elif cls == "BETWEEN" and kind in {"COMPARE_BETWEEN", "COMPARE_NOT_BETWEEN"}:
                fields({"input", "lower", "upper"})
                for key in ("input", "lower", "upper"):
                    expression(e[key], scope)
            elif cls == "CASE" and kind == "CASE_EXPR":
                fields({"case_checks", "else_expr"})
                for item in e["case_checks"]:
                    _fields(item, {"when_expr", "then_expr"})
                    expression(item["when_expr"], scope); expression(item["then_expr"], scope)
                expression(e.get("else_expr"), scope)
            else:
                _reject()

        def qualified(obj, expected):
            if obj is not None:
                _fields(obj, {"path"})
                if obj.get("path") != expected:
                    _reject()

        def orders(items, scope):
            for item in items:
                _fields(item, {"type", "null_order", "expression"})
                if item.get("type") not in {"ORDER_DEFAULT", "ASCENDING", "DESCENDING"} or item.get("null_order") not in {"ORDER_DEFAULT", "NULLS_FIRST", "NULLS_LAST"}:
                    _reject()
                expression(item["expression"], scope)

        def order_modifier(item, scope):
            if item:
                _fields(item, {"type", "orders"})
                if item.get("type") != "ORDER_MODIFIER":
                    _reject()
                orders(item.get("orders", []), scope)

        def table(t, scope):
            kind = t.get("type")
            if t.get("sample"):
                _reject()
            if kind == "EMPTY":
                _fields(t, _TABLE_COMMON)
            elif kind == "BASE_TABLE":
                _fields(t, _TABLE_COMMON | {"schema_name", "table_name", "column_name_alias", "catalog_name", "at_clause", "qualified_name"})
                if t.get("catalog_name") or t.get("at_clause"):
                    _reject()
                name = _name(t.get("table_name"))
                schema = _name(t["schema_name"]) if t.get("schema_name") else ""
                qualified(t.get("qualified_name"), ([t["schema_name"]] if schema else []) + [t["table_name"]])
                if not schema and name in scope:
                    return
                if (schema, name) not in policy.relations:
                    _reject()
                refs.add((schema, name))
            elif kind == "JOIN":
                _fields(t, _TABLE_COMMON | {"left", "right", "condition", "join_type", "ref_type", "using_columns", "delim_flipped", "duplicate_eliminated_columns", "is_implicit", "ranking_expression", "nearest_count", "nearest_order_type", "nearest_approx"})
                if t.get("join_type") not in {"INNER", "LEFT", "RIGHT", "OUTER", "SEMI", "ANTI"} or t.get("ref_type") not in {"REGULAR", "CROSS"} or t.get("delim_flipped") or t.get("duplicate_eliminated_columns"):
                    _reject()
                if t.get("ranking_expression") or t.get("nearest_count", 1) != 1 or t.get("nearest_order_type", "ASCENDING") != "ASCENDING" or t.get("nearest_approx"):
                    _reject()
                table(t["left"], scope); table(t["right"], scope)
                expression(t.get("condition"), scope)
            elif kind == "SUBQUERY":
                _fields(t, _TABLE_COMMON | {"subquery", "column_name_alias"})
                sub = t["subquery"]
                _fields(sub, {"node", "named_param_map"})
                parameter_map(sub.get("named_param_map", []))
                query(sub["node"], scope)
            else:
                _reject()

        def query(q, inherited):
            _fields(q, {"type", "modifiers", "cte_map", "select_list", "from_table", "where_clause", "group_expressions", "group_sets", "aggregate_handling", "having", "sample", "qualify"})
            if q.get("type") != "SELECT_NODE" or q.get("sample") or q.get("aggregate_handling") != "STANDARD_HANDLING":
                _reject()
            group_sets = q.get("group_sets", [])
            if group_sets and group_sets != [list(range(len(q.get("group_expressions", []))))]:
                _reject()
            scope = set(inherited)
            ctes = q.get("cte_map", {"map": []})
            _fields(ctes, {"map"})
            for entry in ctes.get("map", []):
                _fields(entry, {"key", "value"})
                name = _name(entry["key"])
                if name in scope or name in {t for _, t in policy.relations}:
                    _reject()
                value = entry["value"]
                _fields(value, {"aliases", "materialized", "key_targets", "payload_aggregates", "query_node"})
                if value.get("materialized") not in {"CTE_MATERIALIZE_DEFAULT", "CTE_MATERIALIZE_ALWAYS", "CTE_MATERIALIZE_NEVER"} or value.get("key_targets") or value.get("payload_aggregates"):
                    _reject()
                query(value["query_node"], scope)
                scope.add(name)
            table(q["from_table"], scope)
            for e in q["select_list"] + q.get("group_expressions", []):
                expression(e, scope)
            for key in ("where_clause", "having", "qualify"):
                expression(q.get(key), scope)
            for modifier in q.get("modifiers", []):
                kind = modifier.get("type")
                if kind == "ORDER_MODIFIER":
                    order_modifier(modifier, scope)
                elif kind == "LIMIT_MODIFIER":
                    _fields(modifier, {"type", "limit", "offset", "limit_type"})
                    if modifier.get("limit_type") != "ROW_COUNT":
                        _reject()
                    expression(modifier.get("limit"), scope); expression(modifier.get("offset"), scope)
                elif kind == "DISTINCT_MODIFIER":
                    _fields(modifier, {"type", "distinct_on_targets"})
                    for e in modifier.get("distinct_on_targets", []):
                        expression(e, scope)
                else:
                    _reject()
        query(statement["node"], set())
        return ValidatedSelect(frozenset(refs), frozenset(funcs))
    except SqlPolicyError:
        raise
    except Exception:
        raise SqlPolicyError() from None


def validate_bindings(conn, validated, policy):
    """Trusted direct metadata only, in the same read transaction as execution."""
    try:
        identity = conn.execute("SELECT system.main.current_database(),system.main.current_schema(),system.main.current_setting('search_path')").fetchone()
        if identity != ("reader", "main", "reader.main"):
            _reject()
        # Normalize in Python using ASCII only. No macro can spoof this operation.
        for name, internal in conn.execute("SELECT function_name,internal FROM system.main.duckdb_functions()").fetchall():
            if isinstance(name, str) and name.isascii() and name.lower() in GUARDED_FUNCTIONS and internal is not True:
                _reject()
        tables = {(d.lower(), s.lower(), n.lower()) for d, s, n in conn.execute(
            "SELECT database_name,schema_name,table_name FROM system.main.duckdb_tables() WHERE internal = false").fetchall()}
        views = {(d.lower(), s.lower(), n.lower()) for d, s, n in conn.execute(
            "SELECT database_name,schema_name,view_name FROM system.main.duckdb_views()").fetchall()}
        for schema, name in validated.relations:
            key = ("reader", schema, name)
            if (schema, name) not in policy.relations or key not in tables or key in views:
                _reject()
            # Any same-name temp object is refused, even if qualified SQL would
            # normally bypass it. There is no user session initialization hook.
            if ("temp", "main", name) in tables or ("temp", "main", name) in views:
                _reject()
    except SqlPolicyError:
        raise
    except Exception:
        raise SqlPolicyError() from None
