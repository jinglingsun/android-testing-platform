from __future__ import annotations

import json
import random
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .actions import Action, candidates, weighted_choice
from .algorithms import AlgorithmContext, StateGraph, load_algorithms
from .config import OUTPUTS_DIR, TestConfig
from .control import clear_control, get_control
from .database import connect, insert
from .device import DeviceAdapter
from .false_positives import matching_rule_id
from .properties import InconclusiveProperty, PreconditionFailed, PropertyContext, load_properties, property_precondition_satisfied, validate_property
from .reports import render_error_report, render_summary_report
from .state import contains_package, iter_nodes, parse_ui_xml, state_hash


RUNNING_DEVICES: set[str] = set()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_task(task_id: int, config: TestConfig) -> None:
    if config.device_id in RUNNING_DEVICES:
        raise RuntimeError(f"device {config.device_id} already has a running task")
    RUNNING_DEVICES.add(config.device_id)
    try:
        _run_task(task_id, config)
    finally:
        RUNNING_DEVICES.discard(config.device_id)
        clear_control(task_id)


def _run_task(task_id: int, config: TestConfig) -> None:
    rng = random.Random(config.seed)
    graph = StateGraph()
    algorithms = load_algorithms()
    explorer = algorithms[config.algorithm]
    properties = load_properties() if config.enable_properties and config.property_probability > 0 else {}
    property_counts = {name: 0 for name in properties}
    property_state: dict = {}
    run_dir = OUTPUTS_DIR / f"task-{task_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    device = DeviceAdapter(config.device_id, config.min_action_interval_sec)
    control = get_control(task_id)
    package = device.install_apk(config.apk_path)
    with connect() as db:
        db.execute("update tasks set package_name=?, status=? where id=?", (package, "running", task_id))
        db.commit()

    consecutive_recovery_failures = 0
    for case_index in range(config.test_cases):
        try:
            system_event_counter = 0
            for action in device.reset_app_actions():
                device.perform(action)
                _record_event(
                    task_id,
                    case_index,
                    system_event_counter,
                    "system",
                    action.to_dict(),
                    run_dir,
                    device,
                    marker="SYSTEM_RECOVERY",
                )
                system_event_counter += 1
            if config.auto_allow_permissions:
                for _ in range(5):
                    action = device.permission_allow_action()
                    if action is None:
                        break
                    device.perform(action)
                    _record_event(
                        task_id,
                        case_index,
                        system_event_counter,
                        "system",
                        action.to_dict(),
                        run_dir,
                        device,
                        marker="PERMISSION_ALLOW",
                    )
                    system_event_counter += 1
            consecutive_recovery_failures = 0
        except Exception:
            consecutive_recovery_failures += 1
            if consecutive_recovery_failures >= 3 or case_index == 0:
                _finish(task_id, "failed")
                _write_summary_report(task_id, run_dir)
                return
            continue

        event_counter = 0
        while event_counter < config.events_per_case:
            control.wait_if_paused()
            if control.stop_event.is_set():
                _finish(task_id, "stopped")
                _write_summary_report(task_id, run_dir)
                return
            wait_start = time.monotonic()
            xml = device.wait_stable(config.stable_poll_ms, config.stable_samples, config.stable_timeout_sec)
            wait_elapsed = time.monotonic() - wait_start
            root = parse_ui_xml(xml)
            packages = _hierarchy_packages(root)
            current_package = package if package in packages else None
            current_hash = state_hash(xml)
            _record_diagnostic(
                task_id,
                case_index,
                event_counter,
                "dump",
                current_package=current_package,
                target_package=package,
                state_hash_value=current_hash,
                hierarchy_packages=packages,
                message=f"dumped hierarchy before choosing action in {wait_elapsed:.2f}s",
            )
            if not contains_package(root, package):
                xml, root = _recover_or_redump_target_ui(task_id, case_index, event_counter, package, device, run_dir, config, xml, root)
                if root is None:
                    event_counter += 1
                    continue
                packages = _hierarchy_packages(root)
                current_package = package if package in packages else None
                current_hash = state_hash(xml)

            before_hash = current_hash
            graph.mark_visit(before_hash)
            action_list = candidates(root, target_package=package)
            _record_diagnostic(
                task_id,
                case_index,
                event_counter,
                "candidates",
                current_package=current_package,
                target_package=package,
                state_hash_value=before_hash,
                hierarchy_packages=packages,
                candidate_counts=Counter(action.kind for action in action_list),
                message=f"generated {len(action_list)} candidate actions",
            )
            if not action_list:
                break

            applicable_properties, rejected_properties = _applicable_properties(properties, device, property_state)
            applicable_property_names = sorted(applicable_properties)
            property_roll = rng.random() if applicable_properties else None
            if applicable_properties and property_roll is not None and property_roll < config.property_probability:
                selected_property = _next_property_name(applicable_properties, property_counts, rng)
                _record_decision_log(
                    task_id,
                    case_index,
                    event_counter,
                    "property",
                    action_list,
                    applicable_property_names,
                    {
                        "type": "property",
                        "name": selected_property,
                        "reason": (
                            f"性质随机数 {property_roll:.3f} < 概率 {config.property_probability}; "
                            "先筛选前置条件满足的性质，再选择执行次数最少的性质，若并列则随机"
                        ),
                    },
                    config,
                    before_hash,
                    rejected_properties,
                )
                finished_case, used_events = _try_property(
                    task_id,
                    case_index,
                    event_counter,
                    properties,
                    property_counts,
                    device,
                    run_dir,
                    rng,
                    config.auto_allow_permissions,
                    property_state,
                    selected_property,
                )
                event_counter += max(1, used_events)
                if finished_case:
                    break
                continue

            if config.algorithm == "random":
                action = weighted_choice(action_list, config.action_weights, rng)
                reason = f"普通探索；算法=random；按动作权重随机选择，权重={config.action_weights}"
            else:
                action = explorer.choose(before_hash, action_list, AlgorithmContext(rng, graph))
                reason = f"普通探索；算法={config.algorithm}；根据状态图覆盖信息选择"
            if properties and not applicable_properties:
                property_reason = "当前没有前置条件满足的候选性质"
            elif applicable_properties:
                property_reason = f"性质随机数 {property_roll:.3f} >= 概率 {config.property_probability}"
            else:
                property_reason = "未启用性质或没有性质插件"
            _record_decision_log(
                task_id,
                case_index,
                event_counter,
                "action",
                action_list,
                applicable_property_names,
                {
                    "type": "action",
                    "action": action.to_dict(),
                    "reason": f"{property_reason}; {reason}",
                },
                config,
                before_hash,
                rejected_properties,
            )
            _record_diagnostic(
                task_id,
                case_index,
                event_counter,
                "selected_action",
                current_package=current_package,
                target_package=package,
                state_hash_value=before_hash,
                hierarchy_packages=packages,
                candidate_counts=Counter(action.kind for action in action_list),
                selected_action=action.to_dict(),
                message="selected action before execution",
            )

            device.perform(action)
            event_id, after_hash = _record_event(
                task_id,
                case_index,
                event_counter,
                "explore",
                action.to_dict(),
                run_dir,
                device,
                pre_state_hash=before_hash,
            )
            graph.add_edge(before_hash, action, after_hash)

            crash = device.target_fatal_exception()
            if crash:
                suppressed = _record_error(task_id, case_index, event_id, "crash", _crash_fingerprint(crash), "鐩爣搴旂敤宕╂簝", crash, run_dir)
                if not suppressed:
                    break
            for recovery_action in device.ensure_in_target_app():
                _record_event(task_id, case_index, event_counter, "system", recovery_action.to_dict(), run_dir, device, marker="SYSTEM_RECOVERY")
                _record_diagnostic(
                    task_id,
                    case_index,
                    event_counter,
                    "recovery",
                    current_package=device.current_package(),
                    target_package=package,
                    selected_action=recovery_action.to_dict(),
                    message="performed recovery after action left target app",
                )
            event_counter += 1

    _finish(task_id, "finished")
    _write_summary_report(task_id, run_dir)


def _write_summary_report(task_id: int, run_dir: Path) -> None:
    with connect() as db:
        task = dict(db.execute("select * from tasks where id=?", (task_id,)).fetchone())
        errors = [dict(row) for row in db.execute("select * from errors where task_id=?", (task_id,))]
        events = [dict(row) for row in db.execute("select * from events where task_id=? order by id", (task_id,))]
        diagnostics = [dict(row) for row in db.execute("select * from diagnostics where task_id=? order by id", (task_id,))]
    render_summary_report(task, errors, run_dir / "index.html", events, diagnostics)


def _recover_or_redump_target_ui(task_id, case_index, event_counter, package, device, run_dir: Path, config, xml, root):
    current_package = device.current_package()
    if current_package == package:
        recovery_timeout = min(config.stable_timeout_sec, 2.0)
        recovery_samples = min(config.stable_samples, 2)
        for attempt in range(3):
            xml = device.wait_stable(config.stable_poll_ms, recovery_samples, recovery_timeout)
            root = parse_ui_xml(xml)
            packages = _hierarchy_packages(root)
            _record_diagnostic(
                task_id,
                case_index,
                event_counter,
                "redump",
                current_package=current_package,
                target_package=package,
                state_hash_value=state_hash(xml),
                hierarchy_packages=packages,
                message=f"redump attempt {attempt + 1} after hierarchy missed target package",
            )
            if contains_package(root, package):
                return xml, root
        _record_marker(
            task_id,
            case_index,
            event_counter,
            "DUMP_IGNORED",
            {
                "reason": "this hierarchy snapshot does not contain target package controls",
                "current_package": current_package,
                "target_package": package,
                "hierarchy_packages": sorted({node.package for node in iter_nodes(root) if node.package}),
            },
        )
        _record_snapshot_event(
            task_id,
            case_index,
            event_counter,
            "diagnostic",
            {
                "kind": "dump_ignored",
                "reason": "this hierarchy snapshot does not contain target package controls",
                "current_package": current_package,
                "target_package": package,
                "hierarchy_packages": sorted({node.package for node in iter_nodes(root) if node.package}),
            },
            run_dir,
            device,
            xml,
            marker="DUMP_IGNORED",
        )
        close_overlay = Action("back", system=True)
        device.perform(close_overlay)
        _record_event(
            task_id,
            case_index,
            event_counter,
            "system",
            close_overlay.to_dict(),
            run_dir,
            device,
            marker="CLOSE_SYSTEM_OVERLAY",
        )
        _record_diagnostic(
            task_id,
            case_index,
            event_counter,
            "close_overlay",
            current_package=current_package,
            target_package=package,
            selected_action=close_overlay.to_dict(),
            message="pressed back after repeated non-target hierarchy snapshots while current app stayed target",
        )
        xml = device.wait_stable(config.stable_poll_ms, recovery_samples, recovery_timeout)
        root = parse_ui_xml(xml)
        packages = _hierarchy_packages(root)
        _record_diagnostic(
            task_id,
            case_index,
            event_counter,
            "post_overlay_close_dump",
            current_package=package if package in packages else current_package,
            target_package=package,
            state_hash_value=state_hash(xml),
            hierarchy_packages=packages,
            message="dumped hierarchy after closing possible system overlay",
        )
        if contains_package(root, package):
            return xml, root
        return xml, None

    recovery_actions = device.ensure_in_target_app()
    if not recovery_actions:
        forced_restart = Action("restart", selector={"packageName": package}, system=True)
        device.perform(forced_restart)
        recovery_actions = [forced_restart]
    for recovery_action in recovery_actions:
        _record_event(
            task_id,
            case_index,
            event_counter,
            "system",
            recovery_action.to_dict(),
            run_dir,
            device,
            marker="SYSTEM_RECOVERY",
        )
        _record_diagnostic(
            task_id,
            case_index,
            event_counter,
            "recovery",
            current_package=device.current_package(),
            target_package=package,
            selected_action=recovery_action.to_dict(),
            message="performed recovery before choosing action",
        )
    xml = device.wait_stable(config.stable_poll_ms, min(config.stable_samples, 2), min(config.stable_timeout_sec, 3.0))
    root = parse_ui_xml(xml)
    packages = _hierarchy_packages(root)
    _record_diagnostic(
        task_id,
        case_index,
        event_counter,
        "post_recovery_dump",
        current_package=package if package in packages else None,
        target_package=package,
        state_hash_value=state_hash(xml),
        hierarchy_packages=packages,
        message="dumped hierarchy after recovery",
    )
    if not contains_package(root, package):
        return xml, None
    return xml, root


def _next_property_name(properties, property_counts, rng) -> str:
    scoped_counts = {name: property_counts.get(name, 0) for name in properties}
    min_count = min(scoped_counts.values()) if scoped_counts else 0
    choices = [name for name, count in scoped_counts.items() if count == min_count]
    return rng.choice(choices)


def _applicable_properties(properties, device, property_state: dict) -> tuple[dict, dict[str, str]]:
    applicable = {}
    rejected = {}
    for name, fn in sorted(properties.items()):
        ok, reason = property_precondition_satisfied(fn, device, name, state=property_state)
        if ok:
            applicable[name] = fn
        else:
            rejected[name] = reason
    return applicable, rejected


def _try_property(
    task_id,
    case_index,
    event_index,
    properties,
    property_counts,
    device,
    run_dir,
    rng,
    auto_allow_permissions: bool,
    property_state: dict,
    selected_property: str | None = None,
) -> tuple[bool, int]:
    name = selected_property or _next_property_name(properties, property_counts, rng)
    fn = properties[name]
    validate_property(fn)
    property_counts[name] += 1
    used_events = 0

    def recorder(action, kind="property"):
        nonlocal used_events
        _record_event(task_id, case_index, event_index + used_events, kind, action.to_dict(), run_dir, device)
        used_events += 1

    ctx = PropertyContext(device, recorder, name, auto_allow_permissions=auto_allow_permissions, state=property_state)
    _record_marker(task_id, case_index, event_index, "PROPERTY_START", {"property": name})
    try:
        fn(ctx)
        _record_marker(task_id, case_index, event_index, "PROPERTY_END", {"property": name, "status": "passed"})
        _record_property_result(task_id, case_index, event_index, name, "passed", "性质执行完成，最终断言通过")
        return False, used_events
    except PreconditionFailed as exc:
        _record_marker(task_id, case_index, event_index, "PROPERTY_END", {"property": name, "status": "not_applicable"})
        _record_property_result(task_id, case_index, event_index, name, "not_applicable", f"前置条件不满足，性质不适用：{exc}")
        return False, used_events
    except InconclusiveProperty as exc:
        _record_marker(task_id, case_index, event_index, "PROPERTY_END", {"property": name, "status": "inconclusive"})
        _record_property_result(task_id, case_index, event_index, name, "inconclusive", f"事件序列未能完整执行，不计为错误：{exc}")
        return False, used_events
    except AssertionError as exc:
        xml = device.dump_xml()
        assertion_type = str(exc).split(":", 1)[0]
        fingerprint = f"{name}:{assertion_type}:{state_hash(xml)}"
        event_id, _ = _record_event(
            task_id,
            case_index,
            event_index + used_events,
            "property",
            {"property": name, "assertion": str(exc)},
            run_dir,
            device,
        )
        suppressed = _record_error(
            task_id,
            case_index,
            event_id,
            "property",
            fingerprint,
            f"性质违反：{name}",
            str(exc),
            run_dir,
            property_name=name,
        )
        _record_marker(task_id, case_index, event_index, "PROPERTY_END", {"property": name, "status": "failed"})
        _record_property_result(task_id, case_index, event_index, name, "failed", f"最终断言失败，记录为性质违反：{exc}")
        return not suppressed, used_events + 1
    except Exception as exc:
        _record_marker(task_id, case_index, event_index, "PROPERTY_END", {"property": name, "status": "exception"})
        _record_property_result(task_id, case_index, event_index, name, "exception", f"性质执行抛出异常，不计为性质违反：{exc}")
        return False, used_events


def _record_event(task_id, case_index, event_index, kind, action_json, run_dir: Path, device: DeviceAdapter, marker=None, pre_state_hash=None):
    stamp = datetime.now().strftime("%H%M%S%f")
    shot = run_dir / f"case-{case_index:03d}-event-{event_index:04d}-{kind}-{stamp}.png"
    xml_path = run_dir / f"case-{case_index:03d}-event-{event_index:04d}-{kind}-{stamp}.xml"
    capture_start = time.monotonic()
    xml = device.wait_stable(300, 1, 2)
    capture_elapsed = time.monotonic() - capture_start
    xml_path.write_text(xml, encoding="utf-8")
    device.screenshot(shot)
    _write_highlight_image(shot, action_json)
    post_hash = state_hash(xml)
    with connect() as db:
        event_id = insert(
            db,
            "events",
            {
                "task_id": task_id,
                "case_index": case_index,
                "event_index": event_index,
                "kind": kind,
                "action_json": json.dumps(action_json, ensure_ascii=False),
                "pre_state_hash": pre_state_hash,
                "post_state_hash": post_hash,
                "screenshot_path": str(shot),
                "hierarchy_path": str(xml_path),
                "marker": marker,
                "created_at": now(),
            },
        )
        insert(
            db,
            "diagnostics",
            {
                "task_id": task_id,
                "case_index": case_index,
                "event_index": event_index,
                "stage": "capture",
                "state_hash": post_hash,
                "hierarchy_packages": json.dumps(_hierarchy_packages(parse_ui_xml(xml)), ensure_ascii=False),
                "selected_action": json.dumps(action_json, ensure_ascii=False),
                "message": f"captured post-action screenshot and hierarchy in {capture_elapsed:.2f}s",
                "created_at": now(),
            },
        )
    return event_id, post_hash


def _record_snapshot_event(task_id, case_index, event_index, kind, action_json, run_dir: Path, device: DeviceAdapter, xml: str, marker=None, pre_state_hash=None):
    stamp = datetime.now().strftime("%H%M%S%f")
    shot = run_dir / f"case-{case_index:03d}-event-{event_index:04d}-{kind}-{stamp}.png"
    xml_path = run_dir / f"case-{case_index:03d}-event-{event_index:04d}-{kind}-{stamp}.xml"
    xml_path.write_text(xml, encoding="utf-8")
    device.screenshot(shot)
    _write_highlight_image(shot, action_json)
    post_hash = state_hash(xml)
    with connect() as db:
        event_id = insert(
            db,
            "events",
            {
                "task_id": task_id,
                "case_index": case_index,
                "event_index": event_index,
                "kind": kind,
                "action_json": json.dumps(action_json, ensure_ascii=False),
                "pre_state_hash": pre_state_hash,
                "post_state_hash": post_hash,
                "screenshot_path": str(shot),
                "hierarchy_path": str(xml_path),
                "marker": marker,
                "created_at": now(),
            },
        )
    return event_id, post_hash


def _record_marker(task_id, case_index, event_index, marker, payload):
    with connect() as db:
        insert(
            db,
            "events",
            {
                "task_id": task_id,
                "case_index": case_index,
                "event_index": event_index,
                "kind": "marker",
                "action_json": json.dumps(payload, ensure_ascii=False),
                "pre_state_hash": None,
                "post_state_hash": None,
                "screenshot_path": None,
                "hierarchy_path": None,
                "marker": marker,
                "created_at": now(),
            },
        )


def _record_diagnostic(
    task_id,
    case_index,
    event_index,
    stage,
    current_package=None,
    target_package=None,
    state_hash_value=None,
    hierarchy_packages=None,
    candidate_counts=None,
    selected_action=None,
    message=None,
):
    with connect() as db:
        insert(
            db,
            "diagnostics",
            {
                "task_id": task_id,
                "case_index": case_index,
                "event_index": event_index,
                "stage": stage,
                "current_package": current_package,
                "target_package": target_package,
                "state_hash": state_hash_value,
                "hierarchy_packages": json.dumps(hierarchy_packages or [], ensure_ascii=False),
                "candidate_counts": json.dumps(dict(candidate_counts or {}), ensure_ascii=False),
                "selected_action": json.dumps(selected_action or {}, ensure_ascii=False),
                "message": message,
                "created_at": now(),
            },
        )


def _record_decision_log(
    task_id,
    case_index,
    event_index,
    decision_type: str,
    action_list: list[Action],
    property_names: list[str],
    selected: dict,
    config: TestConfig,
    state_hash_value: str,
    rejected_properties: dict[str, str] | None = None,
) -> None:
    action_candidates = [_action_summary(action) for action in action_list[:30]]
    payload = {
        "decision_type": decision_type,
        "algorithm": config.algorithm,
        "action_weights": config.action_weights,
        "candidate_action_count": len(action_list),
        "candidate_actions": action_candidates,
        "candidate_action_counts": dict(Counter(action.kind for action in action_list)),
        "candidate_properties": property_names,
        "rejected_properties": rejected_properties or {},
        "property_probability": config.property_probability,
        "selected": selected,
    }
    _record_diagnostic(
        task_id,
        case_index,
        event_index,
        "decision",
        state_hash_value=state_hash_value,
        candidate_counts=Counter(action.kind for action in action_list),
        selected_action=payload,
        message=selected.get("reason", ""),
    )


def _record_property_result(task_id, case_index, event_index, name: str, status: str, message: str) -> None:
    _record_diagnostic(
        task_id,
        case_index,
        event_index,
        "property_result",
        selected_action={"property": name, "status": status},
        message=message,
    )


def _action_summary(action: Action) -> dict:
    selector = action.selector or {}
    target_parts = []
    for key in ("resourceId", "description", "text", "className"):
        value = selector.get(key)
        if value:
            target_parts.append(f"{key}={value}")
    if action.bounds:
        target_parts.append(f"bounds={action.bounds}")
    if action.coordinates:
        target_parts.append(f"xy={action.coordinates}")
    return {
        "kind": action.kind,
        "target": ", ".join(target_parts) or "无目标控件",
    }


def _highlight_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}-highlight{source.suffix}")


def _write_highlight_image(source: Path, action: dict) -> None:
    target = _highlight_path(source)
    try:
        with Image.open(source) as img:
            draw = ImageDraw.Draw(img)
            width, height = img.size
            bounds = _coerce_bounds(action.get("bounds"))
            if bounds:
                draw.rectangle(bounds, outline="#d93025", width=max(6, width // 120))
                cx = (bounds[0] + bounds[2]) // 2
                cy = (bounds[1] + bounds[3]) // 2
                radius = max(10, width // 80)
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline="#d93025", width=max(4, width // 180))
            else:
                label = _action_label(action)
                pad = max(12, width // 80)
                box = (pad, pad, min(width - pad, pad + 520), pad + 96)
                draw.rectangle(box, outline="#d93025", width=max(6, width // 120))
                draw.text((box[0] + 18, box[1] + 26), label, fill="#d93025", font=_label_font())
            img.save(target)
    except Exception:
        return


def _coerce_bounds(value) -> tuple[int, int, int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            left, top, right, bottom = (int(part) for part in value)
        except Exception:
            return None
        if right > left and bottom > top:
            return left, top, right, bottom
    return None


def _action_label(action: dict) -> str:
    kind = action.get("kind") or "event"
    if kind == "back":
        return "BACK"
    if kind == "swipe":
        return "SWIPE"
    if kind == "restart":
        return "RESTART APP"
    if kind == "force_stop":
        return "FORCE STOP"
    if kind == "clear_app":
        return "CLEAR APP"
    if kind == "clear_logcat":
        return "CLEAR LOGCAT"
    if kind == "permission_allow":
        return "ALLOW PERMISSION"
    return kind.upper()


def _label_font():
    try:
        return ImageFont.truetype("arial.ttf", 32)
    except Exception:
        return ImageFont.load_default()


def _hierarchy_packages(root) -> list[str]:
    return sorted({node.package for node in iter_nodes(root) if node.package})


def _record_error(task_id, case_index, event_id, error_type, fingerprint, title, detail, run_dir: Path, property_name: str | None = None) -> bool:
    with connect() as db:
        duplicate = db.execute(
            "select id, suppressed from errors where task_id=? and fingerprint=? and error_type=?",
            (task_id, fingerprint, error_type),
        ).fetchone()
        if duplicate:
            return bool(duplicate["suppressed"])

        rule_id = matching_rule_id(db, error_type, fingerprint, property_name)
        error_id = insert(
            db,
            "errors",
            {
                "task_id": task_id,
                "case_index": case_index,
                "event_id": event_id,
                "error_type": error_type,
                "fingerprint": fingerprint,
                "title": title,
                "detail": detail,
                "suppressed": 1 if rule_id else 0,
                "false_positive_rule_id": rule_id,
                "created_at": now(),
            },
        )
        events = [
            dict(row)
            for row in db.execute(
                "select * from events where task_id=? and case_index=? order by id",
                (task_id, case_index),
            )
        ]
        report = render_error_report(error_id, title, detail, events, run_dir / f"error-{error_id}.html")
        db.execute("update errors set report_path=? where id=?", (str(report), error_id))
        db.commit()
        return bool(rule_id)


def _crash_fingerprint(logcat: str) -> str:
    lines = [line.strip() for line in logcat.splitlines() if line.strip()]
    exception = next((line for line in lines if "Exception" in line or "Error" in line), "FATAL EXCEPTION")
    frame = next((line for line in lines if line.startswith("at ")), "")
    return f"{exception}|{frame}"


def _finish(task_id: int, status: str) -> None:
    with connect() as db:
        db.execute("update tasks set status=?, finished_at=? where id=?", (status, now(), task_id))
        db.commit()


def _weighted_order(actions, weights, rng):
    pool = list(actions)
    ordered = []
    while pool:
        action = weighted_choice(pool, weights, rng)
        ordered.append(action)
        pool.remove(action)
    return ordered
