from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

JsonDict = dict[str, object]


class QueryVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical_name: str
    description: str | None = None
    info_object: str | None = None
    processing_type: str | None = None
    mandatory: bool | None = None
    default_value: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class QueryKeyFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical_name: str
    description: str | None = None
    formula: str | None = None
    selections: list[JsonDict] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class QueryProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    object_type: str = "UNKNOWN"
    role: str = "provider"
    href: str | None = None


class QueryAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    description: str | None = None
    variables: list[QueryVariable] = Field(default_factory=list)
    calculated_key_figures: list[QueryKeyFigure] = Field(default_factory=list)
    restricted_key_figures: list[QueryKeyFigure] = Field(default_factory=list)
    filters: list[JsonDict] = Field(default_factory=list)
    layout: JsonDict = Field(default_factory=dict)
    providers: list[QueryProvider] = Field(default_factory=list)
    local_members: list[JsonDict] = Field(default_factory=list)
    fields: list[JsonDict] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


def parse_query_xml(xml: str, *, source: str = "") -> QueryAnalysisResult:
    """Parse BW Modeling query XML into deterministic, citation-friendly metadata.

    The parser intentionally extracts only stable object/field references from local XML. It does
    not call BW, execute queries, or require an LLM/network.
    """

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        query_id = _source_query_value(source, "queryName") or _source_query_value(source, "query")
        if query_id is None:
            raise ValueError("query XML is not parseable and source has no queryName") from exc
        return QueryAnalysisResult(
            query_id=query_id,
            metadata={"unknown_reason": "PARSER_UNSUPPORTED"},
        )

    root_fields = _fields(root)
    query_id = (
        _first_text(root_fields, "technicalName", "name", "queryName", "objectName")
        or _source_query_value(source, "queryName")
        or _source_query_value(source, "query")
    )
    if query_id is None:
        query_id = _first_query_id(root) or "UNKNOWN_QUERY"

    metadata: JsonDict = {"source": source} if source else {}
    active = _active_from_source_or_xml(source, root_fields)
    if active is not None:
        metadata["active"] = active
        if active is False:
            metadata["fallback"] = "inactive"

    variables = _dedupe_variables(_parse_variables(root))
    calculated = _dedupe_key_figures(_parse_key_figures(root, kind="calculated"))
    restricted = _dedupe_key_figures(_parse_key_figures(root, kind="restricted"))
    filters = _dedupe_records(_parse_filters(root))
    layout = _parse_layout(root)
    providers = _dedupe_providers(_parse_providers(root, query_id=query_id))
    local_members = _dedupe_records(_parse_local_members(root))
    fields = _dedupe_records(
        [
            *_fields_from_variables(variables),
            *_fields_from_key_figures(calculated, role="calculated_key_figure"),
            *_fields_from_key_figures(restricted, role="restricted_key_figure"),
            *_fields_from_filters(filters),
            *_fields_from_layout(layout),
            *_fields_from_local_members(local_members),
        ]
    )

    return QueryAnalysisResult(
        query_id=query_id,
        description=_first_text(root_fields, "description", "label", "text"),
        variables=variables,
        calculated_key_figures=calculated,
        restricted_key_figures=restricted,
        filters=filters,
        layout=layout,
        providers=providers,
        local_members=local_members,
        fields=fields,
        metadata=metadata,
    )


def _parse_variables(root: ET.Element[str]) -> list[QueryVariable]:
    variables: list[QueryVariable] = []
    for element in root.iter():
        local = _local_name(element.tag).lower()
        fields = _fields(element)
        type_hint = _first_text(fields, "type", "objectType", "kind", "usage") or ""
        if "variable" not in local and "variable" not in type_hint.lower():
            continue
        technical_name = _first_text(
            fields,
            "technicalName",
            "variableName",
            "name",
            "id",
        )
        if technical_name is None:
            continue
        variables.append(
            QueryVariable(
                technical_name=technical_name,
                description=_first_text(fields, "description", "label", "text"),
                info_object=_first_text(fields, "infoObject", "characteristic", "reference"),
                processing_type=_first_text(
                    fields, "processingType", "replacementPath", "inputType"
                ),
                mandatory=_bool_value(_first_text(fields, "mandatory", "required")),
                default_value=_first_text(fields, "defaultValue", "value", "low"),
                metadata=_compact_metadata(
                    fields, exclude={"technicalName", "variableName", "name", "id"}
                ),
            )
        )
    return variables


def _parse_key_figures(root: ET.Element[str], *, kind: str) -> list[QueryKeyFigure]:
    figures: list[QueryKeyFigure] = []
    local_needles = (
        ("calculated", "formula", "ckf")
        if kind == "calculated"
        else ("restricted", "selection", "rkf")
    )
    for element in root.iter():
        local = _local_name(element.tag).lower()
        fields = _fields(element)
        type_hint = " ".join(
            _text(fields.get(key)) or "" for key in ("type", "objectType", "kind", "role")
        ).lower()
        if not any(needle in local or needle in type_hint for needle in local_needles):
            continue
        technical_name = _first_text(fields, "technicalName", "keyFigureName", "name", "id")
        if technical_name is None:
            continue
        selections = _selection_records(element)
        figures.append(
            QueryKeyFigure(
                technical_name=technical_name,
                description=_first_text(fields, "description", "label", "text"),
                formula=_first_text(fields, "formula", "expression", "calculation"),
                selections=selections,
                metadata=_compact_metadata(
                    fields, exclude={"technicalName", "keyFigureName", "name", "id"}
                ),
            )
        )
    return figures


def _parse_filters(root: ET.Element[str]) -> list[JsonDict]:
    filters: list[JsonDict] = []
    for element in root.iter():
        local = _local_name(element.tag).lower()
        if "filter" not in local and "condition" not in local:
            continue
        fields = _fields(element)
        record = _selection_record(fields)
        if record:
            filters.append(record)
    return filters


def _parse_layout(root: ET.Element[str]) -> JsonDict:
    layout: JsonDict = {}
    for axis_name in ("rows", "columns", "free"):
        values: list[str] = []
        for element in root.iter():
            local = _local_name(element.tag).lower()
            role = (_first_text(_fields(element), "axis", "role", "type") or "").lower()
            if local != axis_name and role != axis_name.rstrip("s") and role != axis_name:
                continue
            for candidate in _axis_values(element):
                if candidate not in values:
                    values.append(candidate)
        if values:
            layout[axis_name] = values
    return layout


def _parse_providers(root: ET.Element[str], *, query_id: str) -> list[QueryProvider]:
    providers: list[QueryProvider] = []
    for element in root.iter():
        local = _local_name(element.tag).lower()
        fields = _fields(element)
        href = _first_text(fields, "href", "HREF", "ref")
        parsed = _provider_from_href(href) if href else None
        explicit_provider = _is_explicit_provider_element(local, fields)
        provider_id: str | None
        provider_type: str
        if parsed is not None and _is_related_provider_link(local, fields):
            provider_id, provider_type = parsed
        elif explicit_provider:
            provider_id = _first_text(
                fields,
                "provider",
                "providerName",
                "baseProvider",
                "infoprovider",
                "infoProvider",
                "objectName",
                "technicalName",
                "name",
            )
            provider_type = (
                _first_text(fields, "providerType", "objectType", "type") or "UNKNOWN"
            )
        else:
            continue
        if provider_id is None:
            continue
        if provider_id.upper() == query_id.upper():
            continue
        if provider_type.upper() in {"VARIABLE", "CKF", "RKF"}:
            continue
        providers.append(QueryProvider(object_id=provider_id, object_type=provider_type, href=href))
    return providers


def _is_related_provider_link(local: str, fields: dict[str, object]) -> bool:
    if local == "link":
        rel = (_first_text(fields, "rel") or "").lower()
        return rel in {"", "related", "provider"}
    return False


def _is_explicit_provider_element(local: str, fields: dict[str, object]) -> bool:
    if "provider" in local or "infoprovider" in local:
        return True
    lower_fields = {key.lower() for key in fields}
    return any(
        key.lower() in lower_fields
        for key in ("provider", "providerName", "baseProvider", "infoprovider", "infoProvider")
    )


def _parse_local_members(root: ET.Element[str]) -> list[JsonDict]:
    members: list[JsonDict] = []
    for element in root.iter():
        local = _local_name(element.tag).lower()
        fields = _fields(element)
        role = (_first_text(fields, "role", "type", "kind") or "").lower()
        if "member" not in local and "member" not in role:
            continue
        technical_name = _first_text(fields, "technicalName", "memberName", "name", "id")
        if technical_name is None:
            continue
        members.append(
            _drop_none(
                {
                    "technical_name": technical_name,
                    "description": _first_text(fields, "description", "label", "text"),
                    "formula": _first_text(fields, "formula", "expression", "calculation"),
                    "role": role or None,
                }
            )
        )
    return members


def _selection_records(element: ET.Element[str]) -> list[JsonDict]:
    records: list[JsonDict] = []
    for child in element.iter():
        if child is element:
            continue
        local = _local_name(child.tag).lower()
        if "selection" not in local and "filter" not in local and "condition" not in local:
            continue
        record = _selection_record(_fields(child))
        if record:
            records.append(record)
    return _dedupe_records(records)


def _selection_record(fields: dict[str, object]) -> JsonDict:
    info_object = _first_text(fields, "infoObject", "characteristic", "field", "reference")
    low = _first_text(fields, "low", "value", "from", "member")
    high = _first_text(fields, "high", "to")
    operator = _first_text(fields, "operator", "comparison", "sign")
    if info_object is None and low is None and high is None:
        return {}
    return _drop_none(
        {
            "info_object": info_object,
            "operator": operator,
            "value": low,
            "high": high,
        }
    )


def _axis_values(element: ET.Element[str]) -> list[str]:
    values: list[str] = []
    fields = _fields(element)
    direct = _first_text(fields, "infoObject", "characteristic", "member", "name", "technicalName")
    if direct is not None and _local_name(element.tag).lower() not in {"rows", "columns", "free"}:
        values.append(direct)
    for child in element.iter():
        if child is element:
            continue
        child_fields = _fields(child)
        value = _first_text(
            child_fields,
            "infoObject",
            "characteristic",
            "member",
            "technicalName",
            "name",
        )
        if value is not None and value not in values:
            values.append(value)
    return values


def _fields_from_variables(variables: Iterable[QueryVariable]) -> list[JsonDict]:
    return [
        _drop_none(
            {
                "name": variable.technical_name,
                "role": "variable",
                "description": variable.description,
                "info_object": variable.info_object,
                "source_component": variable.technical_name,
            }
        )
        for variable in variables
    ]


def _fields_from_key_figures(figures: Iterable[QueryKeyFigure], *, role: str) -> list[JsonDict]:
    return [
        _drop_none(
            {
                "name": figure.technical_name,
                "role": role,
                "description": figure.description,
                "formula": figure.formula,
            }
        )
        for figure in figures
    ]


def _fields_from_filters(filters: Iterable[JsonDict]) -> list[JsonDict]:
    fields: list[JsonDict] = []
    for item in filters:
        name = _text(item.get("info_object"))
        if name:
            fields.append({"name": name, "role": "filter"})
    return fields


def _fields_from_layout(layout: JsonDict) -> list[JsonDict]:
    fields: list[JsonDict] = []
    for role in ("rows", "columns", "free"):
        values = layout.get(role)
        if isinstance(values, list):
            for value in values:
                name = _text(value)
                if name:
                    fields.append({"name": name, "role": f"layout_{role}"})
    return fields


def _fields_from_local_members(members: Iterable[JsonDict]) -> list[JsonDict]:
    fields: list[JsonDict] = []
    for item in members:
        name = _text(item.get("technical_name"))
        if name:
            fields.append(
                {"name": name, "role": "local_member", "formula": item.get("formula") or ""}
            )
    return fields


def _active_from_source_or_xml(source: str, fields: dict[str, object]) -> bool | None:
    source_active = _source_query_value(source, "active")
    if source_active is not None:
        parsed = _bool_value(source_active)
        if parsed is not None:
            return parsed
    version = _first_text(fields, "version", "objectVersion", "active")
    if version is None:
        if urlparse(source).path.rstrip("/").endswith("/m"):
            return False
        if urlparse(source).path.rstrip("/").endswith("/a"):
            return True
        return None
    if version.lower() in {"m", "modified", "inactive"}:
        return False
    if version.lower() in {"a", "active"}:
        return True
    return _bool_value(version)


def _provider_from_href(href: str | None) -> tuple[str, str] | None:
    if not href:
        return None
    path = urlparse(href).path.strip("/")
    parts = [part for part in path.split("/") if part]
    for index, part in enumerate(parts):
        token = part.lower()
        if token in {
            "hcpr",
            "adso",
            "query",
            "rsds",
            "infocube",
            "cube",
            "compositeprovider",
        } and index + 1 < len(parts):
            object_type = {
                "hcpr": "HCPR",
                "adso": "ADSO",
                "query": "QUERY",
                "rsds": "RSDS",
                "infocube": "INFOCUBE",
                "cube": "CUBE",
                "compositeprovider": "HCPR",
            }[token]
            return (parts[index + 1].upper(), object_type)
    return None


def _first_query_id(root: ET.Element[str]) -> str | None:
    for element in root.iter():
        value = _first_text(_fields(element), "technicalName", "queryName", "name")
        if value is not None:
            return value
    return None


def _dedupe_variables(items: Iterable[QueryVariable]) -> list[QueryVariable]:
    values: dict[str, QueryVariable] = {}
    for item in items:
        values.setdefault(item.technical_name, item)
    return list(values.values())


def _dedupe_key_figures(items: Iterable[QueryKeyFigure]) -> list[QueryKeyFigure]:
    values: dict[str, QueryKeyFigure] = {}
    for item in items:
        values.setdefault(item.technical_name, item)
    return list(values.values())


def _dedupe_providers(items: Iterable[QueryProvider]) -> list[QueryProvider]:
    values: dict[tuple[str, str], QueryProvider] = {}
    for item in items:
        values.setdefault((item.object_id, item.object_type), item)
    return list(values.values())


def _dedupe_records(items: Iterable[JsonDict]) -> list[JsonDict]:
    values: dict[str, JsonDict] = {}
    for item in items:
        if not item:
            continue
        key = repr(sorted(item.items(), key=lambda part: part[0]))
        values.setdefault(key, item)
    return list(values.values())


def _compact_metadata(fields: dict[str, object], *, exclude: set[str]) -> JsonDict:
    return {
        key: value
        for key, value in fields.items()
        if key not in exclude and isinstance(value, str) and value.strip()
    }


def _drop_none(record: dict[str, object | None]) -> JsonDict:
    return {key: value for key, value in record.items() if value is not None and value != ""}


def _fields(element: ET.Element[str]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in element.attrib.items():
        if value.strip():
            fields[_local_name(key)] = value.strip()
    text = (element.text or "").strip()
    if text:
        fields["text"] = text
    for child in list(element):
        local = _local_name(child.tag)
        text_value = "".join(child.itertext()).strip()
        if text_value:
            fields.setdefault(local, text_value)
        for key, value in child.attrib.items():
            if value.strip() and key.lower().endswith("href"):
                fields.setdefault("href", value.strip())
    return fields


def _first_text(item: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _text(item.get(key))
        if value is not None:
            return value
    lower = {key.lower(): value for key, value in item.items()}
    for key in keys:
        value = _text(lower.get(key.lower()))
        if value is not None:
            return value
    return None


def _source_query_value(source: str, key: str) -> str | None:
    wanted = key.lower()
    for raw_key, values in parse_qs(urlparse(source).query).items():
        if raw_key.lower() == wanted and values:
            return values[0]
    return None


def _bool_value(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "x", "1", "yes", "active"}:
        return True
    if normalized in {"false", "", "0", "no", "inactive", "m", "modified"}:
        return False
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _local_name(name: str) -> str:
    if "}" in name:
        return name.rsplit("}", maxsplit=1)[1]
    if ":" in name:
        return name.rsplit(":", maxsplit=1)[1]
    return name
