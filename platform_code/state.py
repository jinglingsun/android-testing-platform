from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DYNAMIC_PATTERNS = [
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b"), "<uuid>"),
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b"), "<time>"),
    (re.compile(r"\b\d+\b"), "<num>"),
]


@dataclass(frozen=True)
class UiNode:
    package: str
    class_name: str
    resource_id: str
    description: str
    text: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool
    long_clickable: bool
    scrollable: bool
    editable: bool
    children: tuple["UiNode", ...]

    @property
    def is_leaf(self) -> bool:
        return not self.children


def parse_bounds(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    nums = [int(x) for x in re.findall(r"\d+", raw)]
    if len(nums) != 4:
        return None
    return nums[0], nums[1], nums[2], nums[3]


def clean_text(value: str | None) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    for pattern, replacement in DYNAMIC_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def parse_ui_xml(xml_text: str) -> UiNode:
    if not xml_text:
        raise ValueError("UI hierarchy XML is empty")
    root = ET.fromstring(xml_text)
    if root.tag == "hierarchy":
        return UiNode(
            package="",
            class_name="hierarchy",
            resource_id="",
            description="",
            text="",
            bounds=None,
            clickable=False,
            long_clickable=False,
            scrollable=False,
            editable=False,
            children=tuple(_parse_node(child) for child in root if child.attrib.get("visible-to-user", "true") != "false"),
        )
    return _parse_node(root)


def _parse_node(elem: ET.Element) -> UiNode:
    children = tuple(_parse_node(child) for child in elem if child.attrib.get("visible-to-user", "true") != "false")
    return UiNode(
        package=elem.attrib.get("package", ""),
        class_name=elem.attrib.get("class", ""),
        resource_id=_short_resource(elem.attrib.get("resource-id", "")),
        description=clean_text(elem.attrib.get("content-desc")),
        text=clean_text(elem.attrib.get("text")),
        bounds=parse_bounds(elem.attrib.get("bounds")),
        clickable=elem.attrib.get("clickable") == "true",
        long_clickable=elem.attrib.get("long-clickable") == "true",
        scrollable=elem.attrib.get("scrollable") == "true",
        editable=elem.attrib.get("class", "").endswith("EditText") or elem.attrib.get("password") == "true",
        children=children,
    )


def _short_resource(resource_id: str) -> str:
    if not resource_id:
        return ""
    return resource_id.split("/", 1)[-1]


def normalized_repr(node: UiNode) -> str:
    return _repr_node(_collapse_repeated_siblings(node))


def state_hash(xml_text: str) -> str:
    return hashlib.sha256(normalized_repr(parse_ui_xml(xml_text)).encode("utf-8")).hexdigest()[:16]


def _repr_node(node: UiNode) -> str:
    attrs = [
        node.class_name,
        node.resource_id,
        node.description,
        node.text,
        f"c={int(node.clickable)}",
        f"l={int(node.long_clickable)}",
        f"s={int(node.scrollable)}",
        f"e={int(node.editable)}",
    ]
    children = ",".join(_repr_node(child) for child in node.children)
    return f"({ '|'.join(attrs) }[{children}])"


def _collapse_repeated_siblings(node: UiNode) -> UiNode:
    seen: set[str] = set()
    children: list[UiNode] = []
    for child in node.children:
        collapsed = _collapse_repeated_siblings(child)
        signature = _repr_node(collapsed)
        if signature not in seen:
            seen.add(signature)
            children.append(collapsed)
    return UiNode(
        node.package,
        node.class_name,
        node.resource_id,
        node.description,
        node.text,
        node.bounds,
        node.clickable,
        node.long_clickable,
        node.scrollable,
        node.editable,
        tuple(children),
    )


def iter_leaf_nodes(node: UiNode) -> Iterable[UiNode]:
    if node.is_leaf:
        yield node
    for child in node.children:
        yield from iter_leaf_nodes(child)


def iter_nodes(node: UiNode) -> Iterable[UiNode]:
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def contains_package(node: UiNode, package_name: str) -> bool:
    return any(child.package == package_name for child in iter_nodes(node))
