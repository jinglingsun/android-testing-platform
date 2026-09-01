from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from .actions import Action

from .config import PLUGIN_PROPERTY_DIR


class PreconditionFailed(AssertionError):
    pass


class InconclusiveProperty(Exception):
    pass


class PreconditionSatisfied(Exception):
    pass


@dataclass
class PropertyResult:
    name: str
    status: str
    assertion: str | None = None
    detail: str | None = None


class PropertyContext:
    def __init__(
        self,
        device,
        recorder,
        property_name: str,
        auto_allow_permissions: bool = False,
        state: dict | None = None,
    ) -> None:
        self.d = device.d
        self.device = device
        self.recorder = recorder
        self.property_name = property_name
        self.auto_allow_permissions = auto_allow_permissions
        self.state = state if state is not None else {}
        self._action_seen = False
        self._final_seen = False

    def require_exists(self, **selector) -> None:
        if not self.d(**selector).exists:
            if self._action_seen:
                raise InconclusiveProperty(f"require_exists failed: {selector}")
            raise PreconditionFailed(f"precondition failed: {selector}")

    def require_count(self, expected: int, **selector) -> None:
        count = self.count(**selector)
        if count != expected:
            if self._action_seen:
                raise InconclusiveProperty(f"require_count failed: {selector}, expected {expected}, got {count}")
            raise PreconditionFailed(f"precondition failed: {selector}, expected {expected}, got {count}")

    def require_not_exists(self, **selector) -> None:
        if self.count(**selector) > 0:
            if self._action_seen:
                raise InconclusiveProperty(f"require_not_exists failed: {selector}")
            raise PreconditionFailed(f"precondition failed, object exists: {selector}")

    def count(self, **selector) -> int:
        return _selector_count_from_xml(self.device.dump_xml(), selector)

    def texts(self, **selector) -> list[str]:
        return _selector_texts_from_xml(self.device.dump_xml(), selector)

    def tap(self, **selector) -> None:
        self._action_seen = True
        obj = self.d(**selector)
        if not obj.exists:
            raise InconclusiveProperty(f"action target missing before tap: {selector}")
        info = obj.info
        bounds = _bounds_from_info(info)
        coordinates = _center(bounds) if bounds else None
        action = Action("click", selector, bounds, coordinates)
        self.device.perform(action)
        self.recorder(action, "property")
        self._auto_allow_permission_popups()

    def long_click(self, **selector) -> None:
        self._action_seen = True
        obj = self.d(**selector)
        if not obj.exists:
            raise InconclusiveProperty(f"action target missing before long_click: {selector}")
        info = obj.info
        bounds = _bounds_from_info(info)
        coordinates = _center(bounds) if bounds else None
        action = Action("long_click", selector, bounds, coordinates)
        self.device.perform(action)
        self.recorder(action, "property")
        self._auto_allow_permission_popups()

    def set_text(self, text: str, **selector) -> None:
        self._action_seen = True
        obj = self.d(**selector)
        if not obj.exists:
            raise InconclusiveProperty(f"action target missing before set_text: {selector}")
        info = obj.info
        bounds = _bounds_from_info(info)
        coordinates = _center(bounds) if bounds else None
        action = Action("input", selector, bounds, coordinates, text)
        self.device.perform(action)
        self.recorder(action, "property")
        self._auto_allow_permission_popups()

    def press_back(self) -> None:
        self._action_seen = True
        action = Action("back")
        self.device.perform(action)
        self.recorder(action, "property")
        self._auto_allow_permission_popups()

    def _auto_allow_permission_popups(self) -> None:
        if not self.auto_allow_permissions:
            return
        for action in self.device.auto_allow_permissions():
            self.recorder(action, "system")

    def final_assert_exists(self, **selector) -> None:
        self._final_seen = True
        assert self.d(**selector).exists, f"final_assert_exists failed: {selector}"

    def final_assert_not_exists(self, **selector) -> None:
        self._final_seen = True
        assert not self.d(**selector).exists, f"final_assert_not_exists failed: {selector}"

    def final_assert_count(self, expected: int, **selector) -> None:
        self._final_seen = True
        count = self.count(**selector)
        assert count == expected, f"final_assert_count failed: {selector}, expected {expected}, got {count}"


class PreconditionProbeContext(PropertyContext):
    def __init__(self, device, property_name: str, state: dict | None = None) -> None:
        super().__init__(device, lambda action, kind="property": None, property_name, state=state)

    def tap(self, **selector) -> None:
        raise PreconditionSatisfied()

    def long_click(self, **selector) -> None:
        raise PreconditionSatisfied()

    def set_text(self, text: str, **selector) -> None:
        raise PreconditionSatisfied()

    def press_back(self) -> None:
        raise PreconditionSatisfied()

    def final_assert_exists(self, **selector) -> None:
        raise PreconditionSatisfied()

    def final_assert_not_exists(self, **selector) -> None:
        raise PreconditionSatisfied()

    def final_assert_count(self, expected: int, **selector) -> None:
        raise PreconditionSatisfied()


def property_precondition_satisfied(fn: Callable[[PropertyContext], None], device, name: str, state: dict | None = None) -> tuple[bool, str]:
    validate_property(fn)
    ctx = PreconditionProbeContext(device, name, state=state)
    try:
        fn(ctx)
        return True, "前置条件满足：性质函数在探测模式下未执行任何动作"
    except PreconditionSatisfied:
        return True, "前置条件满足：探测到性质即将执行事件序列"
    except PreconditionFailed as exc:
        return False, f"前置条件不满足：{exc}"
    except InconclusiveProperty as exc:
        return False, f"前置探测不确定：{exc}"
    except AssertionError as exc:
        return False, f"前置探测断言失败：{exc}"
    except Exception as exc:
        return False, f"前置探测异常：{exc}"


def load_properties() -> dict[str, Callable[[PropertyContext], None]]:
    properties: dict[str, Callable[[PropertyContext], None]] = {}
    PLUGIN_PROPERTY_DIR.mkdir(parents=True, exist_ok=True)
    for path in PLUGIN_PROPERTY_DIR.glob("*.py"):
        module = _load_module(path)
        for name, value in vars(module).items():
            if name.startswith("test_") and callable(value):
                properties[f"{path.stem}.{name}"] = value
    return properties


def validate_property(fn: Callable) -> None:
    sig = inspect.signature(fn)
    if len(sig.parameters) != 1:
        raise ValueError(f"{fn.__name__} must accept exactly one PropertyContext argument")


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load property plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bounds_from_info(info: dict) -> tuple[int, int, int, int] | None:
    bounds = info.get("bounds") if isinstance(info, dict) else None
    if not isinstance(bounds, dict):
        return None
    return int(bounds["left"]), int(bounds["top"]), int(bounds["right"]), int(bounds["bottom"])


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def _object_count(obj) -> int:
    count = getattr(obj, "count", None)
    if isinstance(count, int):
        return count
    if callable(count):
        return int(count())
    try:
        return len(obj)
    except TypeError:
        return 1 if obj.exists else 0


def _selector_count_from_xml(xml: str, selector: dict) -> int:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return _object_count_fallback(selector)
    return sum(1 for node in root.iter("node") if _node_matches_selector(node.attrib, selector))


def _selector_texts_from_xml(xml: str, selector: dict) -> list[str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    return [node.attrib.get("text", "") for node in root.iter("node") if _node_matches_selector(node.attrib, selector)]


def _object_count_fallback(selector: dict) -> int:
    return 0


def _node_matches_selector(attrs: dict, selector: dict) -> bool:
    for key, expected in selector.items():
        if expected is None:
            continue
        actual = _node_attr(attrs, key)
        if str(actual) != str(expected):
            return False
    return True


def _node_attr(attrs: dict, key: str) -> str:
    if key == "resourceId":
        return attrs.get("resource-id", "")
    if key == "description":
        return attrs.get("content-desc", "")
    if key == "className":
        return attrs.get("class", "")
    if key == "packageName":
        return attrs.get("package", "")
    return attrs.get(key, "")
