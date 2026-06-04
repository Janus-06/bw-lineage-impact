from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp


class FieldConfidence(StrEnum):
    DIRECT = "direct"
    EXPRESSION = "expression"
    ROUTINE_OPAQUE = "routine_opaque"
    UNKNOWN = "unknown"


class SqlConfidence(StrEnum):
    SQL_PARSED = "sql_parsed"
    SQL_UNKNOWN = "sql_unknown"


class FieldEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    transformation_id: str
    source_object_id: str
    source_field: str | None = None
    target_object_id: str
    target_field: str
    confidence: FieldConfidence
    expression: str | None = None
    routine_id: str | None = None
    evidence_fragment_id: str


class FieldLineageDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    transformation_id: str
    source_object_id: str
    target_object_id: str
    field_edges: list[FieldEdge]


class SqlViewNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object_type: str = "NATIVE_SQL_VIEW"


class SqlFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    text: str


class SqlColumnReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    table_alias: str | None
    column_name: str
    expression: str


class SqlReferenceEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_object_id: str
    target_object_id: str
    type: str = "sql_reference"
    confidence: SqlConfidence = SqlConfidence.SQL_PARSED
    evidence_fragment_ids: list[str] = Field(default_factory=list)


class SqlParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    view: SqlViewNode
    confidence: SqlConfidence
    parser: str
    reference_edges: list[SqlReferenceEdge]
    columns: list[SqlColumnReference]
    fragments: list[SqlFragment]


FieldOutputFormat = Literal["json", "md"]
SqlOutputFormat = Literal["json", "md"]


def parse_transformation_mapping_xml(
    xml_text: str,
    *,
    transformation_id: str,
    source_object_id: str,
    target_object_id: str,
) -> FieldLineageDocument:
    root = ElementTree.fromstring(xml_text)
    field_edges: list[FieldEdge] = []
    for index, element in enumerate(root.iter(), start=1):
        attrs = {_normalize_attr(_local_name(key)): value for key, value in element.attrib.items()}
        source_refs = _find_element_refs(element, "source")
        target_ref = _find_element_ref(element, "target")
        step_expression, step_routine_id = _find_step_metadata(element)
        target_field = _first_attr(attrs, "targetfield", "target", "tofield")
        if target_field is None and target_ref is not None:
            target_field = target_ref[1]
        if not target_field:
            continue
        attr_source_field = _first_attr(attrs, "sourcefield", "source", "fromfield")
        if source_refs:
            source_candidates: list[tuple[str | None, str | None]] = [
                (source_ref_object_id, source_ref_field)
                for source_ref_object_id, source_ref_field in source_refs
            ]
        elif attr_source_field:
            source_candidates = [(None, attr_source_field)]
        else:
            source_candidates = [(None, None)]
        rule_routine_type = _first_attr(attrs, "routinetype")
        routine_id = (
            _first_attr(attrs, "routine", "routineid", "routinename", "abaproutine")
            or step_routine_id
            or ("inline_routine" if _is_routine_type(rule_routine_type) else None)
        )
        raw_expression = _first_attr(attrs, "expression", "formula") or step_expression
        expression = None if routine_id else raw_expression
        base_edge_id = f"{transformation_id}:{target_field}:{index}"
        for source_index, (source_ref_object_id, source_field) in enumerate(
            source_candidates,
            start=1,
        ):
            if routine_id:
                confidence = FieldConfidence.ROUTINE_OPAQUE
            elif expression:
                confidence = FieldConfidence.EXPRESSION
            elif source_field:
                confidence = FieldConfidence.DIRECT
            else:
                confidence = FieldConfidence.UNKNOWN
            edge_id = (
                base_edge_id
                if len(source_candidates) == 1
                else f"{base_edge_id}:{source_index}"
            )
            field_edges.append(
                FieldEdge(
                    id=edge_id,
                    transformation_id=transformation_id,
                    source_object_id=_first_attr(attrs, "sourceobject", "sourceobjectid")
                    or source_ref_object_id
                    or source_object_id,
                    source_field=source_field,
                    target_object_id=_first_attr(attrs, "targetobject", "targetobjectid")
                    or (target_ref[0] if target_ref is not None else None)
                    or target_object_id,
                    target_field=target_field,
                    confidence=confidence,
                    expression=expression,
                    routine_id=routine_id,
                    evidence_fragment_id=f"xml:{index}",
                )
            )
    return FieldLineageDocument(
        transformation_id=transformation_id,
        source_object_id=source_object_id,
        target_object_id=target_object_id,
        field_edges=field_edges,
    )


def parse_native_sql_view(sql_text: str, *, view_id: str) -> SqlParseResult:
    try:
        tree = sqlglot.parse_one(sql_text)
    except Exception:
        return _unknown_sql_parse_result(sql_text, view_id=view_id)
    if isinstance(tree, exp.Command):
        return _unknown_sql_parse_result(sql_text, view_id=view_id)

    source_tables = _source_tables(tree, view_id=view_id)
    fragments = _sql_fragments(tree, source_tables=source_tables)
    reference_edges = _sql_reference_edges(
        source_tables,
        view_id=view_id,
        source_fragment_ids=_source_fragment_ids_by_table(fragments),
    )
    columns = _sql_column_references(tree)
    return SqlParseResult(
        view=SqlViewNode(id=view_id),
        confidence=SqlConfidence.SQL_PARSED,
        parser="sqlglot",
        reference_edges=reference_edges,
        columns=columns,
        fragments=fragments,
    )


def render_field_lineage(
    document: FieldLineageDocument,
    *,
    output_format: FieldOutputFormat,
) -> str:
    if output_format == "json":
        return json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    if output_format == "md":
        lines = [
            "# Field Lineage Evidence",
            "",
            f"- Transformation: `{document.transformation_id}`",
            f"- Source object: `{document.source_object_id}`",
            f"- Target object: `{document.target_object_id}`",
            "",
        ]
        for edge in document.field_edges:
            source = edge.expression or edge.routine_id or edge.source_field or "UNKNOWN"
            source_detail = (
                f" source=`{edge.source_field}`"
                if edge.source_field and edge.source_field != source
                else ""
            )
            lines.append(
                f"- `{edge.target_field}` <= `{source}` "
                f"confidence=`{edge.confidence.value}` evidence=`{edge.evidence_fragment_id}`"
                f"{source_detail}"
            )
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported field lineage output format: {output_format}")


def render_sql_view_evidence(result: SqlParseResult, *, output_format: SqlOutputFormat) -> str:
    if output_format == "json":
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    if output_format == "md":
        lines = [
            "# Native SQL View Evidence",
            "",
            f"- View: `{result.view.id}`",
            f"- Parser: `{result.parser}`",
            f"- Confidence: `{result.confidence.value}`",
            "- Boundary: optimization notes are advisory only; "
            "no SQL rewrite or DB change is applied.",
            "",
            "## References",
            "",
        ]
        if result.reference_edges:
            for edge in result.reference_edges:
                lines.append(
                    f"- `{edge.source_object_id}` -> `{edge.target_object_id}` "
                    f"confidence=`{edge.confidence.value}`"
                )
        else:
            lines.append("- No table/view references parsed.")
        lines.extend(["", "## Fragments", ""])
        for fragment in result.fragments:
            lines.append(f"- `{fragment.id}` kind=`{fragment.kind}`")
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported SQL evidence output format: {output_format}")


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _unknown_sql_parse_result(sql_text: str, *, view_id: str) -> SqlParseResult:
    return SqlParseResult(
        view=SqlViewNode(id=view_id),
        confidence=SqlConfidence.SQL_UNKNOWN,
        parser="sqlglot",
        reference_edges=[],
        columns=[],
        fragments=[SqlFragment(id="sql:raw", kind="raw_sql", text=sql_text)],
    )


def _sql_reference_edges(
    source_tables: list[tuple[exp.Table, str]],
    *,
    view_id: str,
    source_fragment_ids: dict[str, str],
) -> list[SqlReferenceEdge]:
    edges: list[SqlReferenceEdge] = []
    for _, table_name in source_tables:
        source_fragment_id = source_fragment_ids.get(table_name)
        evidence_fragment_ids = [source_fragment_id] if source_fragment_id is not None else []
        edges.append(
            SqlReferenceEdge(
                id=f"sqlref:{view_id}:{table_name}",
                source_object_id=table_name,
                target_object_id=view_id,
                evidence_fragment_ids=evidence_fragment_ids,
            )
        )
    return edges


def _source_tables(tree: exp.Expression, *, view_id: str) -> list[tuple[exp.Table, str]]:
    source_tables: list[tuple[exp.Table, str]] = []
    create_targets = _create_target_ids(tree)
    seen: set[str] = set()
    for table in tree.find_all(exp.Table):
        table_name = _qualified_table_id(table)
        if table_name in create_targets or _is_unqualified_cte_reference(table):
            continue
        if table_name == view_id or table_name in seen:
            continue
        seen.add(table_name)
        source_tables.append((table, table_name))
    return source_tables


def _is_unqualified_cte_reference(table: exp.Table) -> bool:
    table_reference_key = _unqualified_table_reference_key(table)
    if table_reference_key is None:
        return False
    containing_cte_alias_key = _containing_cte_alias_key(table)
    for alias_key in _visible_cte_alias_keys(table):
        if table_reference_key == alias_key and table_reference_key != containing_cte_alias_key:
            return True
    return False


def _visible_cte_alias_keys(expression: exp.Expression) -> set[tuple[str, bool]]:
    aliases: set[tuple[str, bool]] = set()
    current: exp.Expression | None = expression
    while current is not None:
        aliases.update(_expression_cte_alias_keys(current))
        current = getattr(current, "parent", None)
    return aliases


def _expression_cte_alias_keys(expression: exp.Expression) -> set[tuple[str, bool]]:
    with_expression = expression.args.get("with_") or expression.args.get("with")
    if not isinstance(with_expression, exp.With):
        return set()
    aliases: set[tuple[str, bool]] = set()
    for cte in with_expression.expressions:
        if isinstance(cte, exp.CTE):
            alias_key = _cte_alias_key(cte)
            if alias_key is not None:
                aliases.add(alias_key)
    return aliases


def _containing_cte_alias_key(table: exp.Table) -> tuple[str, bool] | None:
    parent = getattr(table, "parent", None)
    while parent is not None:
        if isinstance(parent, exp.CTE):
            return _cte_alias_key(parent)
        parent = getattr(parent, "parent", None)
    return None


def _sql_column_references(tree: exp.Expression) -> list[SqlColumnReference]:
    columns: list[SqlColumnReference] = []
    seen: set[tuple[str | None, str, str]] = set()
    for index, column in enumerate(tree.find_all(exp.Column), start=1):
        table_alias = column.table or None
        key = (table_alias, column.name, column.sql())
        if key in seen:
            continue
        seen.add(key)
        columns.append(
            SqlColumnReference(
                id=f"sqlcol:{index}",
                table_alias=table_alias,
                column_name=column.name,
                expression=column.sql(),
            )
        )
    return columns


def _sql_fragments(
    tree: exp.Expression,
    *,
    source_tables: list[tuple[exp.Table, str]],
) -> list[SqlFragment]:
    query = _select_body(tree)
    fragments: list[SqlFragment] = []
    set_operations = (
        [tree] if isinstance(tree, exp.SetOperation) else list(tree.find_all(exp.SetOperation))
    )
    _append_fragments(
        fragments,
        "set_operation",
        (operation.sql() for operation in set_operations),
    )
    _append_fragments(fragments, "join", (join.sql() for join in tree.find_all(exp.Join)))
    _append_fragments(fragments, "where", (where.sql() for where in tree.find_all(exp.Where)))
    _append_fragments(fragments, "group", (group.sql() for group in tree.find_all(exp.Group)))
    _append_fragments(fragments, "function", (func.sql() for func in tree.find_all(exp.Func)))
    _append_fragments(fragments, "subquery", (sub.sql() for sub in tree.find_all(exp.Subquery)))
    if not fragments:
        fragments.append(SqlFragment(id="sqlfrag:select:1", kind="select", text=query.sql()))
    _append_source_fragments(fragments, source_tables)
    return fragments


def _append_fragments(fragments: list[SqlFragment], kind: str, values: Any) -> None:
    for value in values:
        fragments.append(
            SqlFragment(
                id=f"sqlfrag:{kind}:{len([item for item in fragments if item.kind == kind]) + 1}",
                kind=kind,
                text=str(value),
            )
        )


def _append_source_fragments(
    fragments: list[SqlFragment],
    source_tables: list[tuple[exp.Table, str]],
) -> None:
    for _, table_name in source_tables:
        source_index = len([item for item in fragments if item.kind == "source_table"]) + 1
        fragments.append(
            SqlFragment(
                id=f"sqlfrag:source_table:{source_index}",
                kind="source_table",
                text=f"source table: {table_name}",
            )
        )


def _source_fragment_ids_by_table(fragments: list[SqlFragment]) -> dict[str, str]:
    fragment_ids: dict[str, str] = {}
    prefix = "source table: "
    for fragment in fragments:
        if fragment.kind == "source_table" and fragment.text.startswith(prefix):
            table_name = fragment.text.removeprefix(prefix)
            fragment_ids[table_name] = fragment.id
    return fragment_ids


def _normalize_attr(name: str) -> str:
    return "".join(char.lower() for char in name if char.isalnum())


def _first_attr(attrs: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = attrs.get(_normalize_attr(name))
        if value:
            return value
    return None


def _find_element_ref(element: ElementTree.Element, role: str) -> tuple[str | None, str] | None:
    refs = _find_element_refs(element, role)
    return refs[0] if refs else None


def _find_element_refs(element: ElementTree.Element, role: str) -> list[tuple[str | None, str]]:
    role = role.lower()
    refs: list[tuple[str | None, str]] = []
    for child in element:
        if _local_name(child.tag).lower() != role:
            continue
        for descendant in child.iter():
            if _local_name(descendant.tag).lower() == "elementref" and descendant.text:
                parsed = _parse_element_ref(descendant.text)
                if parsed is not None:
                    refs.append(parsed)
    return refs


def _find_step_metadata(element: ElementTree.Element) -> tuple[str | None, str | None]:
    for descendant in element.iter():
        step_tag = _local_name(descendant.tag).lower()
        if step_tag != "step" and not step_tag.startswith("step"):
            continue
        attrs = {
            _normalize_attr(_local_name(key)): value
            for key, value in descendant.attrib.items()
        }
        expression = _first_attr(attrs, "expression", "formula") or _step_child_text(
            descendant,
            "formula",
        )
        code = _step_child_text(descendant, "code")
        class_name = _first_attr(attrs, "classnamem", "classname", "class")
        method_name = _first_attr(attrs, "methodnamem", "methodname", "method")
        step_type = (_first_attr(attrs, "type", "xsitype") or step_tag).lower()
        routine_named_attr = _first_attr(attrs, "routine", "routineid", "routinename")
        routine_fallback = routine_named_attr or (
            _first_attr(attrs, "id") if "routine" in step_type else None
        )
        if "constant" in step_type:
            constant_value = _first_attr(attrs, "constant", "value") or _step_child_text(
                descendant,
                "constant",
                "value",
            )
            if constant_value:
                stripped_constant_value = constant_value.strip()
                if stripped_constant_value:
                    return f"CONSTANT {stripped_constant_value}", None
        if "routine" in step_type or routine_named_attr or class_name or method_name:
            routine_id = ".".join(part for part in (class_name, method_name) if part)
            resolved_routine_id = routine_id or routine_fallback
            if resolved_routine_id:
                return None, resolved_routine_id
            return None, "inline_routine"
        expression = expression or code
        if expression:
            return expression, None
    return None, None


def _step_child_text(element: ElementTree.Element, *names: str) -> str | None:
    wanted = {_normalize_attr(name) for name in names}
    for child in element:
        if _normalize_attr(_local_name(child.tag)) in wanted and child.text:
            text = child.text.strip()
            if text:
                return text
    return None


def _select_body(tree: exp.Expression) -> exp.Expression:
    if isinstance(tree, exp.Select):
        return tree
    select = next(tree.find_all(exp.Select), None)
    return select if select is not None else tree


def _parse_element_ref(value: str) -> tuple[str | None, str] | None:
    parts = [part.strip("#") for part in value.strip().split("/") if part.strip("#")]
    if len(parts) < 2:
        return None
    if parts[0].lower() in {"source", "target"}:
        return None, parts[-1]
    return parts[-2], parts[-1]


def _qualified_table_id(table: exp.Table) -> str:
    parts: list[str] = []
    catalog = _expression_name(table.args.get("catalog"))
    db = _expression_name(table.args.get("db"))
    if catalog:
        parts.append(catalog)
    if db:
        parts.append(db)
    parts.append(table.name)
    return ".".join(part for part in parts if part)


def _create_target_ids(tree: exp.Expression) -> set[str]:
    targets: set[str] = set()
    creates = [tree] if isinstance(tree, exp.Create) else list(tree.find_all(exp.Create))
    for create in creates:
        target = create.this
        if isinstance(target, exp.Schema):
            target = target.this
        if isinstance(target, exp.Table):
            targets.add(_qualified_table_id(target))
    return targets


def _is_routine_type(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized in {"routine", "start", "end", "expert"} or normalized.endswith("routine")


def _cte_alias_keys(tree: exp.Expression) -> set[tuple[str, bool]]:
    aliases: set[tuple[str, bool]] = set()
    for cte in tree.find_all(exp.CTE):
        alias_key = _cte_alias_key(cte)
        if alias_key is not None:
            aliases.add(alias_key)
    return aliases


def _cte_alias_key(cte: exp.CTE) -> tuple[str, bool] | None:
    alias = cte.args.get("alias")
    if not isinstance(alias, exp.TableAlias):
        return None
    return _identifier_reference_key(alias.this)


def _unqualified_table_reference_key(table: exp.Table) -> tuple[str, bool] | None:
    if table.args.get("catalog") is not None or table.args.get("db") is not None:
        return None
    return _identifier_reference_key(table.this)


def _identifier_reference_key(value: object) -> tuple[str, bool] | None:
    if not isinstance(value, exp.Identifier):
        return None
    name = getattr(value, "name", None)
    if not isinstance(name, str) or not name:
        return None
    quoted = bool(value.args.get("quoted"))
    return name if quoted else name.lower(), quoted


def _expression_name(value: object) -> str | None:
    if not isinstance(value, exp.Identifier):
        return None
    name = getattr(value, "name", None)
    return name if isinstance(name, str) and name else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
