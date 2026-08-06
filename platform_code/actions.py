from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Any

from .state import UiNode, iter_leaf_nodes, iter_nodes


@dataclass(frozen=True)
class Action:
    kind: str
    selector: dict[str, Any] | None = None
    bounds: tuple[int, int, int, int] | None = None
    coordinates: tuple[int, int] | None = None
    text: str | None = None
    system: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def selector_for(node: UiNode) -> dict[str, Any]:
    selector: dict[str, Any] = {"className": node.class_name}
    if node.package:
        selector["packageName"] = node.package
    if node.resource_id:
        selector["resourceId"] = node.resource_id
    if node.description:
        selector["description"] = node.description
    if node.text:
        selector["text"] = node.text
    return selector


def candidates(root: UiNode, target_package: str | None = None) -> list[Action]:
    actions: list[Action] = []
    seen: set[tuple[str, tuple[int, int, int, int] | None, tuple[tuple[str, Any], ...]]] = set()
    ordered_nodes = list(iter_leaf_nodes(root)) + [node for node in iter_nodes(root) if not node.is_leaf]
    for node in ordered_nodes:
        for action in _node_actions(node, target_package):
            key = (action.kind, action.bounds, tuple(sorted((action.selector or {}).items())))
            if key not in seen:
                seen.add(key)
                actions.append(action)
    if target_package and not actions:
        return []
    actions.extend(
        [
            Action("swipe", coordinates=(500, 1500)),
            Action("swipe", coordinates=(500, 400)),
            Action("back"),
        ]
    )
    return actions


def _node_actions(node: UiNode, target_package: str | None) -> list[Action]:
    if not node.bounds:
        return []
    if target_package and node.package != target_package:
        return []
    if _is_noise_node(node):
        return []

    node_actions = [Action("click", selector_for(node), node.bounds, center(node.bounds))]
    if node.is_leaf or node.long_clickable:
        node_actions.append(Action("long_click", selector_for(node), node.bounds, center(node.bounds)))
    if node.editable:
        node_actions.append(Action("input", selector_for(node), node.bounds, center(node.bounds), "codex-test"))
    return node_actions


def _is_noise_leaf(node: UiNode) -> bool:
    return _is_noise_node(node)


def _is_noise_node(node: UiNode) -> bool:
    class_name = node.class_name.lower()
    resource_id = node.resource_id.lower()
    if "statusbar" in class_name or "navigationbar" in class_name:
        return True
    if "status_bar" in resource_id or "navigation_bar" in resource_id:
        return True
    if not node.text and not node.description and not node.resource_id and not node.editable:
        return True
    return False


def weighted_choice(actions: list[Action], weights: dict[str, float], rng: random.Random) -> Action:
    if not actions:
        raise ValueError("no actions")
    values = [max(0.0, float(weights.get(action.kind, 1.0))) for action in actions]
    if sum(values) <= 0:
        return rng.choice(actions)
    return rng.choices(actions, weights=values, k=1)[0]
