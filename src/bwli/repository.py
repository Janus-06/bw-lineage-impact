from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

REPOSITORY_BASE_PATH = "/sap/bw/modeling/repo/infoproviderstructure"
REPOSITORY_CHILDREN_PREFIX = f"{REPOSITORY_BASE_PATH}/"


class RepositoryNodeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    parent_path: str = "/"
    path: str
    name: str
    description: str = ""
    object_type: str = "UNKNOWN"
    object_subtype: str | None = None
    status: str | None = None
    has_children: bool = False
    self_url: str | None = None
    fiori_only: bool = False
    children_path: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


def normalize_repository_path(path: str | None) -> str:
    value = (path or "").strip()
    if not value or value == "/":
        return "/"
    normalized = value.lower().strip("/")
    return normalized or "/"


def repository_endpoint_path(path: str | None) -> str:
    normalized = normalize_repository_path(path)
    if normalized == "/":
        return REPOSITORY_BASE_PATH
    return f"{REPOSITORY_BASE_PATH}/{normalized}"


def parse_repository_contents_xml(
    xml: str,
    *,
    parent_path: str | None = None,
) -> list[RepositoryNodeRecord]:
    """Parse SAP BW Modeling repository Atom/XML into deterministic navigation nodes."""

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    parent = normalize_repository_path(parent_path)
    entries = [element for element in root.iter() if _local_xml_name(element.tag) == "entry"]
    nodes = [_repository_node_from_entry(entry, parent_path=parent) for entry in entries]
    return [node for node in nodes if node is not None]


def _repository_node_from_entry(
    entry: ET.Element[str],
    *,
    parent_path: str,
) -> RepositoryNodeRecord | None:
    fields: dict[str, str] = {}
    for element in entry.iter():
        if _local_xml_name(element.tag) == "object":
            for key, value in element.attrib.items():
                text = value.strip()
                if text:
                    fields[_local_xml_name(key)] = text

    name = (
        fields.get("objectName")
        or fields.get("object_name")
        or fields.get("name")
        or _entry_text(entry, "id")
        or ""
    ).strip()
    if not name:
        return None

    description = _entry_text(entry, "title") or fields.get("description") or ""
    object_type = fields.get("objectType") or fields.get("object_type") or "UNKNOWN"
    object_subtype = fields.get("objectSubtype") or fields.get("object_subtype")
    status = fields.get("objectStatus") or fields.get("object_status")
    self_url, self_link_type, children_url = _entry_links(entry)
    fiori_only = bool(
        self_link_type == "application/vnd.sap-bw-modeling.url"
        or (self_url is not None and "#BWProcessChain" in self_url)
    )
    children_path = _children_path(children_url)
    path = children_path or _child_path(parent_path, name)
    metadata: dict[str, object] = {}
    chain_id = _fiori_chain_id(self_url) if fiori_only else None
    if chain_id:
        metadata["chain_id"] = chain_id
    return RepositoryNodeRecord(
        id=path,
        parent_path=parent_path,
        path=path,
        name=name,
        description=description,
        object_type=object_type,
        object_subtype=object_subtype,
        status=status,
        has_children=children_path is not None,
        self_url=self_url,
        fiori_only=fiori_only,
        children_path=children_path,
        metadata=metadata,
    )


def _entry_text(entry: ET.Element[str], local_name: str) -> str | None:
    for element in entry:
        if _local_xml_name(element.tag) != local_name:
            continue
        text = "".join(element.itertext()).strip()
        if text:
            return text
    return None


def _entry_links(entry: ET.Element[str]) -> tuple[str | None, str | None, str | None]:
    self_url: str | None = None
    self_type: str | None = None
    children_url: str | None = None
    for element in entry:
        if _local_xml_name(element.tag) != "link":
            continue
        rel = element.attrib.get("rel", "")
        href = element.attrib.get("href", "").strip() or None
        link_type = element.attrib.get("type", "").strip() or None
        if rel == "self":
            self_url = href
            self_type = link_type
        elif rel == "http://www.sap.com/bw/modeling/relations:children":
            children_url = href
    return self_url, self_type, children_url


def _children_path(children_url: str | None) -> str | None:
    if not children_url:
        return None
    if children_url.startswith(REPOSITORY_CHILDREN_PREFIX):
        return normalize_repository_path(children_url.removeprefix(REPOSITORY_CHILDREN_PREFIX))
    if children_url == REPOSITORY_BASE_PATH:
        return "/"
    parsed = urlparse(children_url)
    if parsed.path.startswith(REPOSITORY_CHILDREN_PREFIX):
        return normalize_repository_path(parsed.path.removeprefix(REPOSITORY_CHILDREN_PREFIX))
    return normalize_repository_path(children_url)


def _child_path(parent_path: str, name: str) -> str:
    safe_name = name.lower().strip("/")
    if parent_path == "/":
        return safe_name
    return f"{parent_path.rstrip('/')}/{safe_name}"


def _fiori_chain_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    # Fiori-only BW repository links often arrive as fragment URLs such as
    # ``#BWProcessChain?chainId=ZCHAIN``. ``urlparse`` keeps that query inside
    # ``fragment`` rather than ``query``, so parse both locations.
    query_texts = [parsed.query]
    if "?" in parsed.fragment:
        query_texts.append(parsed.fragment.split("?", maxsplit=1)[1])
    for query_text in query_texts:
        query = parse_qs(query_text)
        values = query.get("chainId") or query.get("chainid")
        if values:
            return unquote(values[0])
    return None


def _local_xml_name(name: str) -> str:
    if "}" in name:
        return name.rsplit("}", maxsplit=1)[1]
    if ":" in name:
        return name.rsplit(":", maxsplit=1)[1]
    return name
