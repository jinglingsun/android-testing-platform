from __future__ import annotations

import importlib.util
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .actions import Action
from .config import PLUGIN_ALGORITHM_DIR


@dataclass
class StateGraph:
    edges: dict[str, list[tuple[Action, str]]] = field(default_factory=dict)
    visits: dict[str, int] = field(default_factory=dict)
    global_action_counts: dict[tuple, int] = field(default_factory=dict)
    global_target_counts: dict[tuple, int] = field(default_factory=dict)

    def mark_visit(self, state: str) -> None:
        self.visits[state] = self.visits.get(state, 0) + 1

    def add_edge(self, source: str, action: Action, target: str) -> None:
        self.edges.setdefault(source, []).append((action, target))
        action_key = _action_key(action)
        target_key = _target_key(action)
        self.global_action_counts[action_key] = self.global_action_counts.get(action_key, 0) + 1
        self.global_target_counts[target_key] = self.global_target_counts.get(target_key, 0) + 1


class AlgorithmContext:
    def __init__(self, rng: random.Random, graph: StateGraph) -> None:
        self.rng = rng
        self.graph = graph


class Explorer:
    name = "base"

    def choose(self, state_hash: str, actions: list[Action], ctx: AlgorithmContext) -> Action:
        raise NotImplementedError


class RandomExplorer(Explorer):
    name = "random"

    def choose(self, state_hash: str, actions: list[Action], ctx: AlgorithmContext) -> Action:
        return ctx.rng.choice(actions)


class DFSExplorer(Explorer):
    name = "dfs"

    def choose(self, state_hash: str, actions: list[Action], ctx: AlgorithmContext) -> Action:
        return min(_primary_actions(actions), key=lambda action: _coverage_key(ctx.graph, state_hash, action))


class BFSExplorer(Explorer):
    name = "bfs"

    def __init__(self) -> None:
        self.queue: deque[tuple[str, Action]] = deque()

    def choose(self, state_hash: str, actions: list[Action], ctx: AlgorithmContext) -> Action:
        primary_actions = _primary_actions(actions)
        while self.queue:
            queued_state, action = self.queue.popleft()
            if queued_state == state_hash and action in primary_actions:
                return action
        self.queue.extend((state_hash, action) for action in sorted(primary_actions, key=lambda action: _coverage_key(ctx.graph, state_hash, action)))
        return self.queue.popleft()[1]


def _action_seen(graph: StateGraph, state_hash: str, action: Action) -> int:
    return sum(1 for known, _ in graph.edges.get(state_hash, []) if known == action)


def _target_seen(graph: StateGraph, state_hash: str, action: Action) -> int:
    target = _target_key(action)
    return sum(1 for known, _ in graph.edges.get(state_hash, []) if _target_key(known) == target)


def _navigation_penalty(action: Action) -> int:
    if action.kind in {"click", "long_click", "input"}:
        return 0
    if action.kind == "swipe":
        return 1
    if action.kind == "back":
        return 2
    return 3


def _primary_actions(actions: list[Action]) -> list[Action]:
    ui_actions = [action for action in actions if action.kind in {"click", "long_click", "input"}]
    return ui_actions or actions


def _coverage_key(graph: StateGraph, state_hash: str, action: Action) -> tuple[int, ...]:
    return (
        graph.global_target_counts.get(_target_key(action), 0),
        graph.global_action_counts.get(_action_key(action), 0),
        _target_seen(graph, state_hash, action),
        _action_seen(graph, state_hash, action),
        _navigation_penalty(action),
        _kind_penalty(action),
        _semantic_penalty(action),
    )


def _action_key(action: Action) -> tuple:
    return (action.kind, _target_key(action))


def _target_key(action: Action) -> tuple:
    selector = action.selector or {}
    return (
        selector.get("packageName"),
        selector.get("resourceId"),
        selector.get("description"),
        selector.get("text"),
        selector.get("className"),
        tuple(action.bounds or ()),
        tuple(action.coordinates or ()),
    )


def _kind_penalty(action: Action) -> int:
    if action.kind == "click":
        return 0
    if action.kind == "input":
        return 1
    if action.kind == "long_click":
        return 2
    return 3


def _semantic_penalty(action: Action) -> int:
    selector = action.selector or {}
    if selector.get("description") or selector.get("text"):
        return 0
    if selector.get("resourceId"):
        return 1
    return 2


def load_algorithms() -> dict[str, Explorer]:
    algorithms: dict[str, Explorer] = {
        "random": RandomExplorer(),
        "dfs": DFSExplorer(),
        "bfs": BFSExplorer(),
    }
    PLUGIN_ALGORITHM_DIR.mkdir(parents=True, exist_ok=True)
    for path in PLUGIN_ALGORITHM_DIR.glob("*.py"):
        module = _load_module(path)
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Explorer) and value is not Explorer:
                instance = value()
                algorithms[instance.name] = instance
    return algorithms


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load algorithm plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
