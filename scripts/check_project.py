from __future__ import annotations

import compileall
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platform_code.actions import Action, candidates, weighted_choice
from platform_code.algorithms import AlgorithmContext, BFSExplorer, DFSExplorer, StateGraph, load_algorithms
from platform_code.database import connect, init_db
from platform_code.device import _completed_output, _completed_stdout
from platform_code.main import _decorate_event, index, task_detail
from platform_code.properties import PreconditionFailed, PropertyContext, _object_count, _selector_count_from_xml, load_properties, property_precondition_satisfied
from platform_code.reports import render_error_report, render_summary_report
from platform_code.runner import _next_property_name
from platform_code.state import contains_package, parse_ui_xml, state_hash


class ExistingObject:
    exists = True


def main() -> None:
    assert compileall.compile_dir("platform_code", quiet=1)
    assert compileall.compile_dir("plugins", quiet=1)

    init_db()
    with connect() as db:
        error_cols = {row["name"] for row in db.execute("pragma table_info(errors)")}
        replay_cols = {row["name"] for row in db.execute("pragma table_info(replays)")}
        diagnostic_cols = {row["name"] for row in db.execute("pragma table_info(diagnostics)")}
    assert {"suppressed", "false_positive_rule_id"}.issubset(error_cols)
    assert {"error_id", "task_id", "status"}.issubset(replay_cols)
    assert {"stage", "current_package", "hierarchy_packages", "candidate_counts", "selected_action"}.issubset(diagnostic_cols)

    assert "random" in load_algorithms()
    load_properties()

    assert "Android" in index(None)
    assert "Task not found" in task_detail(999999999)
    task_html = task_detail(1)
    assert "实时决策日志" in task_html
    decorated = _decorate_event({"kind": "explore", "action_json": '{"kind":"long_click","selector":{"resourceId":"id/x"}}'})
    assert decorated["action_kind"] == "long_click"
    assert "resourceId=id/x" in decorated["action_target"]
    permission_event = _decorate_event({"kind": "system", "action_json": '{"kind":"permission_allow","selector":{"textMatches":"allow"}}'})
    assert permission_event["action_kind"] == "permission_allow"
    assert "textMatches=allow" in permission_event["action_target"]

    out = Path("outputs") / "self-check"
    out.mkdir(exist_ok=True)
    events = [
        {
            "event_index": 0,
            "kind": "explore",
            "action_json": '{"kind":"back"}',
            "screenshot_path": None,
        }
    ]
    assert render_error_report(1, "title", "detail", events, out / "check-error.html").exists()
    assert render_summary_report(
        {"id": 1, "status": "finished", "device_id": "device", "package_name": "package"},
        [],
        out / "check-summary.html",
        events,
        [{"id": 1, "stage": "dump", "message": "ok"}],
    ).exists()

    xml = '<hierarchy><node class="android.widget.TextView" text="12:30" resource-id="pkg:id/t" bounds="[0,0][10,10]" /></hierarchy>'
    assert state_hash(xml)

    mixed_xml = """
    <hierarchy>
      <node package="target.pkg" class="android.widget.TextView" text="ok" resource-id="target.pkg:id/ok" bounds="[0,0][10,10]" />
      <node package="android" class="android.widget.TextView" text="system" resource-id="android:id/system" bounds="[10,10][20,20]" />
    </hierarchy>
    """
    target_actions = candidates(parse_ui_xml(mixed_xml), target_package="target.pkg")
    assert contains_package(parse_ui_xml(mixed_xml), "target.pkg")
    assert not contains_package(parse_ui_xml(mixed_xml), "missing.pkg")
    assert target_actions
    assert all((action.selector or {}).get("packageName") == "target.pkg" for action in target_actions if action.selector)
    assert candidates(parse_ui_xml(mixed_xml), target_package="missing.pkg") == []

    nav_xml = """
    <hierarchy>
      <node package="target.pkg" class="android.widget.FrameLayout" content-desc="Open navigation" resource-id="" bounds="[0,0][80,80]">
        <node package="target.pkg" class="android.widget.ImageView" text="" resource-id="" bounds="[20,20][60,60]" />
      </node>
    </hierarchy>
    """
    nav_actions = candidates(parse_ui_xml(nav_xml), target_package="target.pkg")
    assert any((action.selector or {}).get("description") == "Open navigation" for action in nav_actions)

    assert _object_count(ExistingObject()) == 1
    count_xml = """
    <hierarchy>
      <node resource-id="de.rampro.activitydiary:id/activity_name" text="Sleeping" />
      <node resource-id="de.rampro.activitydiary:id/activity_name" text="Cooking" />
    </hierarchy>
    """
    assert _selector_count_from_xml(
        count_xml,
        {"resourceId": "de.rampro.activitydiary:id/activity_name", "text": "Sleeping"},
    ) == 1
    class DummyDevice:
        d = None

    def stateful_property(ctx: PropertyContext) -> None:
        if not ctx.state.get("activity_names"):
            raise PreconditionFailed("activity_names set is empty")
        ctx.tap(resourceId="dummy")

    ok, _ = property_precondition_satisfied(stateful_property, DummyDevice(), "stateful", state={"activity_names": {"A"}})
    assert ok
    assert _next_property_name({"only_applicable": object()}, {"rejected": 0, "only_applicable": 10}, random.Random(1)) == "only_applicable"
    assert weighted_choice([Action("back")], {"back": 0}, random.Random(1)).kind == "back"
    dfs_action = DFSExplorer().choose(
        "s",
        [Action("back"), Action("swipe"), Action("click", selector={"resourceId": "id/x"}, bounds=(0, 0, 1, 1), coordinates=(0, 0))],
        AlgorithmContext(random.Random(1), StateGraph()),
    )
    assert dfs_action.kind == "click"
    bfs = BFSExplorer()
    bfs_ctx = AlgorithmContext(random.Random(1), StateGraph())
    assert bfs.choose("old", [Action("swipe"), Action("back")], bfs_ctx).kind == "swipe"
    fresh_click = Action("click", selector={"description": "Open navigation"}, bounds=(0, 0, 1, 1), coordinates=(0, 0))
    assert bfs.choose("new", [fresh_click, Action("swipe"), Action("back")], bfs_ctx) == fresh_click
    graph = StateGraph()
    title = Action("click", selector={"text": "Activity Diary"}, bounds=(0, 0, 10, 10), coordinates=(5, 5))
    cinema = Action("click", selector={"resourceId": "activity_name", "text": "Cinema"}, bounds=(0, 20, 10, 30), coordinates=(5, 25))
    graph.add_edge("old-screen", title, "new-screen")
    bfs2 = BFSExplorer()
    picked = bfs2.choose("new-screen", [title, cinema, Action("swipe"), Action("back")], AlgorithmContext(random.Random(1), graph))
    assert picked == cinema
    proc = subprocess.CompletedProcess(args=[], returncode=0)
    assert _completed_stdout(proc) == ""
    assert _completed_output(proc) == ""

    print("project self-check passed")


if __name__ == "__main__":
    main()
