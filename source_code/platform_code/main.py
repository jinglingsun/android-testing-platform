from __future__ import annotations

import json
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .algorithms import load_algorithms
from .config import PROJECT_ROOT, OUTPUTS_DIR, TestConfig
from .control import get_control
from .database import connect, init_db, insert
from .replay import replay_error
from .runner import run_task


app = FastAPI(title="Android Teaching Test Platform")
templates = Environment(loader=FileSystemLoader(PROJECT_ROOT / "templates"), autoescape=select_autoescape())
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    with connect() as db:
        tasks = [dict(row) for row in db.execute("select * from tasks order by id desc limit 20")]
    tpl = templates.get_template("index.html")
    return tpl.render(tasks=tasks, algorithms=sorted(load_algorithms()))


@app.post("/tasks")
def create_task(
    background: BackgroundTasks,
    device_id: str = Form(...),
    apk_path: str = Form(""),
    test_cases: int = Form(3),
    events_per_case: int = Form(50),
    algorithm: str = Form("random"),
    seed: str = Form(""),
    property_probability: float = Form(0.2),
    enable_properties: str | None = Form(None),
    min_action_interval_sec: float = Form(1.0),
    click_weight: float = Form(0.7),
    long_click_weight: float = Form(0.0),
    swipe_weight: float = Form(0.0),
    input_weight: float = Form(0.15),
    back_weight: float = Form(0.05),
) -> RedirectResponse:
    if algorithm not in load_algorithms():
        return RedirectResponse("/", status_code=303)
    config = TestConfig(
        device_id=device_id.strip(),
        apk_path=apk_path.strip(),
        test_cases=test_cases,
        events_per_case=events_per_case,
        algorithm=algorithm,
        seed=int(seed) if seed.strip() else None,
        property_probability=property_probability,
        enable_properties=enable_properties == "on",
        min_action_interval_sec=min_action_interval_sec,
        action_weights={
            "click": click_weight,
            "long_click": long_click_weight,
            "swipe": swipe_weight,
            "input": input_weight,
            "back": back_weight,
        },
    )
    with connect() as db:
        task_id = insert(
            db,
            "tasks",
            {
                "device_id": config.device_id,
                "apk_path": config.apk_path,
                "package_name": None,
                "status": "queued",
                "config_json": json.dumps(asdict(config), ensure_ascii=False),
                "run_dir": str(OUTPUTS_DIR / "pending"),
                "started_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        db.execute("update tasks set run_dir=? where id=?", (str(OUTPUTS_DIR / f"task-{task_id}"), task_id))
        db.commit()
    background.add_task(_safe_run, task_id, config)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(task_id: int) -> str:
    with connect() as db:
        row = db.execute("select * from tasks where id=?", (task_id,)).fetchone()
        if row is None:
            return "<!doctype html><title>Not found</title><p>Task not found.</p>"
        task = dict(row)
        events = [dict(row) for row in db.execute("select * from events where task_id=? order by id desc limit 15", (task_id,))]
        errors = [dict(row) for row in db.execute("select * from errors where task_id=? order by id", (task_id,))]
        replays = [dict(row) for row in db.execute("select * from replays where task_id=? order by id desc", (task_id,))]
        diagnostics = [dict(row) for row in db.execute("select * from diagnostics where task_id=? order by id desc limit 12", (task_id,))]
        decision_logs = [
            _decorate_decision_log(dict(row))
            for row in db.execute("select * from diagnostics where task_id=? and stage='decision' order by id desc limit 8", (task_id,))
        ]
    events = [_decorate_event(event) for event in events]
    config_view = _config_view(task.get("config_json"))
    summary_exists = (OUTPUTS_DIR / f"task-{task_id}" / "index.html").exists()
    if not summary_exists and task["status"] != "running" and task["status"] != "queued" and task["status"] != "paused":
        _write_summary_after_failure(task_id)
        summary_exists = (OUTPUTS_DIR / f"task-{task_id}" / "index.html").exists()
    tpl = templates.get_template("task.html")
    return tpl.render(task=task, config_view=config_view, events=events, errors=errors, replays=replays, diagnostics=diagnostics, decision_logs=decision_logs, summary_exists=summary_exists)


@app.get("/tasks/{task_id}/live")
def task_live(task_id: int) -> JSONResponse:
    with connect() as db:
        row = db.execute("select * from tasks where id=?", (task_id,)).fetchone()
        if row is None:
            return JSONResponse({"detail": "Task not found"}, status_code=404)
        task = dict(row)
        events = [
            _decorate_event(dict(row))
            for row in db.execute("select * from events where task_id=? order by id desc limit 15", (task_id,))
        ]
        errors = [dict(row) for row in db.execute("select * from errors where task_id=? order by id", (task_id,))]
        diagnostics = [dict(row) for row in db.execute("select * from diagnostics where task_id=? order by id desc limit 12", (task_id,))]
        decision_logs = [
            _decorate_decision_log(dict(row))
            for row in db.execute("select * from diagnostics where task_id=? and stage='decision' order by id desc limit 8", (task_id,))
        ]
        event_count = db.execute("select count(*) from events where task_id=?", (task_id,)).fetchone()[0]
        latest = db.execute(
            "select case_index, event_index from events where task_id=? order by id desc limit 1",
            (task_id,),
        ).fetchone()
        latest_event_id = f"case-{latest['case_index']:03d}-event-{latest['event_index']:04d}" if latest else ""
    return JSONResponse(
        {
            "task": {
                "id": task["id"],
                "status": task["status"],
                "device_id": task["device_id"],
                "package_name": task["package_name"] or "",
            },
            "config": _config_view(task.get("config_json")),
            "summary_exists": (OUTPUTS_DIR / f"task-{task_id}" / "index.html").exists(),
            "event_count": event_count,
            "latest_event_id": latest_event_id,
            "events": events,
            "errors": errors,
            "diagnostics": diagnostics,
            "decision_logs": decision_logs,
        }
    )


@app.post("/errors/{error_id}/false-positive")
async def false_positive(
    error_id: int,
    name: str = Form("璇姤瑙勫垯"),
    match_fingerprint: str | None = Form(None),
    match_error_type: str | None = Form(None),
) -> RedirectResponse:
    with connect() as db:
        err = db.execute("select * from errors where id=?", (error_id,)).fetchone()
        if not match_fingerprint and not match_error_type:
            match_fingerprint = "on"
        rule_id = insert(
            db,
            "false_positive_rules",
            {
                "name": name,
                "rule_json": json.dumps(
                    {
                        "fingerprint": err["fingerprint"] if match_fingerprint else None,
                        "error_type": err["error_type"] if match_error_type else None,
                    },
                    ensure_ascii=False,
                ),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        db.execute("update errors set suppressed=1, false_positive_rule_id=? where id=?", (rule_id, error_id))
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/errors/{error_id}/replay")
async def replay(error_id: int, background: BackgroundTasks) -> RedirectResponse:
    background.add_task(replay_error, error_id)
    return RedirectResponse("/", status_code=303)


@app.post("/tasks/{task_id}/pause")
def pause_task(task_id: int) -> RedirectResponse:
    get_control(task_id).pause_event.set()
    with connect() as db:
        db.execute("update tasks set status=? where id=? and status='running'", ("paused", task_id))
        db.commit()
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: int) -> RedirectResponse:
    get_control(task_id).pause_event.clear()
    with connect() as db:
        db.execute("update tasks set status=? where id=? and status='paused'", ("running", task_id))
        db.commit()
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.post("/tasks/{task_id}/stop")
def stop_task(task_id: int) -> RedirectResponse:
    get_control(task_id).stop_event.set()
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


def _safe_run(task_id: int, config: TestConfig) -> None:
    try:
        run_task(task_id, config)
    except Exception as exc:
        detail = traceback.format_exc(limit=8)
        with connect() as db:
            db.execute("update tasks set status=?, finished_at=? where id=?", (f"failed: {exc}\n{detail}", datetime.now().isoformat(timespec="seconds"), task_id))
            db.commit()
        _write_summary_after_failure(task_id)


def _decorate_event(event: dict) -> dict:
    try:
        action = json.loads(event.get("action_json") or "{}")
    except json.JSONDecodeError:
        action = {}
    event["action_kind"] = action.get("kind") or event.get("kind") or ""
    event["action_target"] = _action_target(action)
    event["screenshot_url"] = _event_screenshot_url(event.get("screenshot_path"))
    display_kind, display_detail = _event_display(event, action)
    event["display_kind"] = display_kind
    event["display_detail"] = display_detail
    return event


def _event_display(event: dict, action: dict) -> tuple[str, str]:
    kind = event.get("kind") or ""
    marker = event.get("marker") or ""
    if kind == "marker":
        property_name = action.get("property") or ""
        status = action.get("status") or ""
        if marker == "PROPERTY_START":
            return "性质开始", property_name
        if marker == "PROPERTY_END":
            return "性质结束", f"{property_name} / {_property_status_label(status)}"
        if marker:
            return "标记", marker
        return "标记", ""
    if marker:
        return _event_kind_label(kind), marker
    return _event_kind_label(kind), ""


def _event_kind_label(kind: str) -> str:
    labels = {
        "property": "性质事件",
        "explore": "探索事件",
        "system": "系统事件",
        "marker": "标记",
    }
    return labels.get(kind, kind)


def _property_status_label(status: str) -> str:
    labels = {
        "passed": "成功",
        "failed": "失败",
        "not_applicable": "前置条件不满足",
        "inconclusive": "事件序列未完整执行",
        "exception": "异常",
    }
    return labels.get(status, status or "未知")


def _decorate_decision_log(item: dict) -> dict:
    try:
        payload = json.loads(item.get("selected_action") or "{}")
    except json.JSONDecodeError:
        payload = {}
    selected = payload.get("selected") or {}
    item["payload"] = payload
    item["decision_type"] = payload.get("decision_type") or ""
    item["candidate_action_count"] = payload.get("candidate_action_count") or 0
    item["candidate_actions"] = payload.get("candidate_actions") or []
    item["candidate_properties"] = payload.get("candidate_properties") or []
    item["rejected_properties"] = payload.get("rejected_properties") or {}
    item["selected_summary"] = _selected_decision_summary(selected)
    item["reason"] = selected.get("reason") or item.get("message") or ""
    return item


def _selected_decision_summary(selected: dict) -> str:
    if selected.get("type") == "property":
        return f"性质：{selected.get('name', '')}"
    if selected.get("type") == "action":
        action = selected.get("action") or {}
        return f"动作：{action.get('kind', '')} / {_action_target(action)}"
    return ""


def _event_screenshot_url(path_value: str | None) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    highlighted = path.with_name(f"{path.stem}-highlight{path.suffix}")
    if highlighted.exists():
        return _output_url(str(highlighted))
    return _output_url(path_value)


def _output_url(path_value: str | None) -> str:
    if not path_value:
        return ""
    try:
        path = Path(path_value)
        relative = path.resolve().relative_to(OUTPUTS_DIR.resolve())
    except Exception:
        return ""
    return "/outputs/" + "/".join(relative.parts)


def _write_summary_after_failure(task_id: int) -> None:
    from .reports import render_summary_report

    run_dir = OUTPUTS_DIR / f"task-{task_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        row = db.execute("select * from tasks where id=?", (task_id,)).fetchone()
        if row is None:
            return
        task = dict(row)
        errors = [dict(row) for row in db.execute("select * from errors where task_id=?", (task_id,))]
        events = [dict(row) for row in db.execute("select * from events where task_id=? order by id", (task_id,))]
        diagnostics = [dict(row) for row in db.execute("select * from diagnostics where task_id=? order by id", (task_id,))]
    render_summary_report(task, errors, run_dir / "index.html", events, diagnostics)


def _action_target(action: dict) -> str:
    selector = action.get("selector") or {}
    parts = []
    for key in ("resourceId", "description", "text", "textMatches", "className", "packageName"):
        value = selector.get(key)
        if value:
            parts.append(f"{key}={value}")
    if action.get("coordinates"):
        parts.append(f"xy={action['coordinates']}")
    if action.get("bounds"):
        parts.append(f"bounds={action['bounds']}")
    return ", ".join(parts)


def _config_view(config_json: str | None) -> list[dict[str, str]]:
    try:
        config = json.loads(config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    weights = config.get("action_weights") or {}
    rows = [
        ("探索算法", config.get("algorithm")),
        ("测试用例数", config.get("test_cases")),
        ("每个用例事件数", config.get("events_per_case")),
        ("性质执行概率", config.get("property_probability")),
        ("启用性质检查", "是" if config.get("enable_properties") else "否"),
        ("动作最小间隔（秒）", config.get("min_action_interval_sec")),
        ("随机种子", config.get("seed") if config.get("seed") is not None else "未设置"),
        ("自动允许权限", "是" if config.get("auto_allow_permissions") else "否"),
        ("稳定等待采样数", config.get("stable_samples")),
        ("稳定等待超时（秒）", config.get("stable_timeout_sec")),
        ("点击权重", weights.get("click")),
        ("长按权重", weights.get("long_click")),
        ("滑动权重", weights.get("swipe")),
        ("输入权重", weights.get("input")),
        ("返回权重", weights.get("back")),
    ]
    return [{"label": label, "value": "" if value is None else str(value)} for label, value in rows]
