from __future__ import annotations

import json
from datetime import datetime

from .actions import Action
from .config import TestConfig
from .database import connect, insert
from .device import DeviceAdapter


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def replay_error(error_id: int) -> None:
    with connect() as db:
        error = db.execute("select * from errors where id=?", (error_id,)).fetchone()
        if error is None:
            return
        task = db.execute("select * from tasks where id=?", (error["task_id"],)).fetchone()
        config = TestConfig(**json.loads(task["config_json"]))
        replay_id = insert(
            db,
            "replays",
            {
                "error_id": error_id,
                "task_id": task["id"],
                "status": "running",
                "detail": None,
                "started_at": now(),
                "finished_at": None,
            },
        )

    try:
        device = DeviceAdapter(config.device_id, config.min_action_interval_sec)
        package = device.install_apk(config.apk_path)
        device.package_name = package

        with connect() as db:
            events = db.execute(
                """
                select events.*
                from events
                join errors on errors.task_id = events.task_id and errors.case_index = events.case_index
                where errors.id=? and events.id <= errors.event_id and events.kind != 'marker'
                order by events.id
                """,
                (error_id,),
            ).fetchall()

        has_recorded_reset = any(_event_action_kind(row) in {"force_stop", "clear_app", "clear_logcat", "restart"} for row in events)
        if not has_recorded_reset:
            device.reset_app()
            if config.auto_allow_permissions:
                device.auto_allow_permissions()

        for row in events:
            payload = json.loads(row["action_json"])
            action = Action(
                kind=payload.get("kind", row["kind"]),
                selector=payload.get("selector"),
                bounds=tuple(payload["bounds"]) if payload.get("bounds") else None,
                coordinates=tuple(payload["coordinates"]) if payload.get("coordinates") else None,
                text=payload.get("text"),
                system=payload.get("system", False),
            )
            device.perform(action)
            device.wait_stable(config.stable_poll_ms, config.stable_samples, config.stable_timeout_sec)

        crash = device.target_fatal_exception()
        detail = "重放完成"
        if error["error_type"] == "crash" and not crash:
            detail = "重放完成，但没有再次观察到目标应用 FATAL EXCEPTION"
        with connect() as db:
            db.execute("update replays set status=?, detail=?, finished_at=? where id=?", ("finished", detail, now(), replay_id))
            db.commit()
    except Exception as exc:
        with connect() as db:
            db.execute("update replays set status=?, detail=?, finished_at=? where id=?", ("failed", str(exc), now(), replay_id))
            db.commit()


def _event_action_kind(row) -> str:
    try:
        return json.loads(row["action_json"] or "{}").get("kind") or row["kind"]
    except json.JSONDecodeError:
        return row["kind"]
