from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw


def render_error_report(error_id: int, title: str, detail: str, events: list[dict], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, event in enumerate(events):
        action = _load_action(event)
        screenshot = event.get("screenshot_path") or ""
        image_name = ""
        if screenshot:
            image_name = _annotated_image(
                Path(screenshot),
                action,
                output.parent,
                final_error=index == len(events) - 1 and event.get("kind") == "property",
            )
        label = _action_label(action)
        image_html = f'<img src="{html.escape(image_name)}" alt="事件截图">' if image_name else ""
        rows.append(
            f"""
            <section class="event">
              <h3>事件 {html.escape(str(event.get('event_index', '?')))}：{html.escape(label)}</h3>
              <pre>{html.escape(json.dumps(action, ensure_ascii=False, indent=2))}</pre>
              {image_html}
            </section>
            """
        )

    output.write_text(
        f"""
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <title>{html.escape(title)}</title>
          <style>
            body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f7f7f5; color: #202124; }}
            main {{ max-width: 1080px; margin: auto; }}
            .error {{ border: 4px solid #d93025; padding: 16px; background: white; border-radius: 8px; }}
            .event {{ margin: 18px 0; padding: 12px; background: white; border: 1px solid #ddd; border-radius: 8px; }}
            img {{ max-width: 360px; border: 1px solid #c9cdd2; }}
            pre {{ white-space: pre-wrap; }}
            button, input {{ padding: 8px 12px; }}
          </style>
        </head>
        <body>
          <main>
            <div class="error">
              <h1>{html.escape(title)}</h1>
              <pre>{html.escape(detail)}</pre>
              <form method="post" action="/errors/{error_id}/false-positive">
                <input name="name" placeholder="误报规则名称">
                <label><input type="checkbox" name="match_fingerprint" checked> 匹配错误指纹</label>
                <label><input type="checkbox" name="match_error_type" checked> 匹配错误类型</label>
                <button type="submit">标记为误报</button>
              </form>
              <form method="post" action="/errors/{error_id}/replay">
                <button type="submit">重放复现</button>
              </form>
            </div>
            {''.join(rows)}
          </main>
        </body>
        </html>
        """,
        encoding="utf-8",
    )
    return output


def render_summary_report(task: dict, errors: list[dict], output: Path, events: list[dict] | None = None, diagnostics: list[dict] | None = None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    events = events or []
    diagnostics = diagnostics or []
    links = "\n".join(
        f"<li><a href='{html.escape(Path(err['report_path']).name)}'>{html.escape(err['title'])}</a></li>"
        for err in errors
        if err.get("report_path")
    )
    if not links:
        links = "<li>未发现错误。</li>"
    case_indices = _case_indices(events, diagnostics, errors)
    case_nav = _case_nav(case_indices, events, diagnostics, errors)
    case_sections = _case_sections(events, diagnostics, errors, output.parent)
    output.write_text(
        f"""
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <title>任务 #{task['id']} 总报告</title>
          <style>
            body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f7f7f5; color: #202124; }}
            main {{ max-width: 1240px; margin: auto; background: white; padding: 20px; border-radius: 8px; }}
            .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }}
            .case {{ margin-top: 24px; padding: 18px; border: 1px solid #dfe3e8; border-radius: 10px; background: #fbfcfe; }}
            .case h2 {{ margin-top: 0; }}
            .case-summary {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 16px; }}
            .case-nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 18px; }}
            .case-nav a {{ display: inline-block; padding: 8px 10px; border: 1px solid #c9cdd2; border-radius: 8px; background: #f8fafd; color: #174ea6; text-decoration: none; }}
            .case-nav a:hover {{ background: #eef3fe; }}
            .pill {{ display: inline-block; padding: 4px 9px; border-radius: 999px; background: #eef3fe; color: #174ea6; font-size: 13px; }}
            .event {{ display: grid; grid-template-columns: 220px 1fr; gap: 16px; padding: 14px 0; border-top: 1px solid #e5e7eb; }}
            .event img {{ width: 220px; border: 1px solid #c9cdd2; border-radius: 6px; }}
            .event pre {{ white-space: pre-wrap; margin: 6px 0 0; font-size: 12px; }}
            .muted {{ color: #687078; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ text-align: left; padding: 7px; border-bottom: 1px solid #e5e7eb; font-size: 13px; vertical-align: top; }}
            details {{ margin-top: 12px; }}
            summary {{ cursor: pointer; font-weight: 600; }}
          </style>
        </head>
        <body>
          <main>
            <h1>任务 #{task['id']}</h1>
            <section class="meta">
              <p>状态：{html.escape(task['status'])}</p>
              <p>设备：{html.escape(task['device_id'])}</p>
              <p>包名：{html.escape(task.get('package_name') or '')}</p>
              <p>事件数：{len(events)}</p>
              <p>测试用例数：{len(case_indices)}</p>
            </section>
            <h2>错误</h2>
            <ul>{links}</ul>
            <h2>选择测试用例</h2>
            {case_nav}
            {case_sections}
          </main>
        </body>
        </html>
        """,
        encoding="utf-8",
    )
    return output


def _case_sections(events: list[dict], diagnostics: list[dict], errors: list[dict], out_dir: Path) -> str:
    indices = _case_indices(events, diagnostics, errors)
    if not indices:
        return "<p>尚未记录测试用例结果。</p>"
    sections = []
    for position, case_index in enumerate(indices):
        case_events = [event for event in events if event.get("case_index") == case_index]
        case_diagnostics = [item for item in diagnostics if item.get("case_index") == case_index]
        case_errors = [err for err in errors if err.get("case_index") == case_index]
        event_rows = "\n".join(_summary_event_row(event, out_dir) for event in case_events)
        if not event_rows:
            event_rows = "<p>该用例尚未记录事件。</p>"
        diagnostic_rows = "\n".join(_diagnostic_row(item) for item in case_diagnostics[-100:])
        if not diagnostic_rows:
            diagnostic_rows = "<tr><td colspan='8'>该用例暂无诊断记录。</td></tr>"
        error_links = "\n".join(_case_error_link(err) for err in case_errors) or "<li>该用例未发现错误。</li>"
        sections.append(
            f"""
            <section class="case" id="case-{html.escape(str(case_index))}">
              <h2>测试用例 {html.escape(str(case_index))}</h2>
              <div class="case-summary">
                <span class="pill">事件 {len(case_events)}</span>
                <span class="pill">诊断 {len(case_diagnostics)}</span>
                <span class="pill">错误 {len(case_errors)}</span>
              </div>
              <p><a href="#top">返回测试用例目录</a></p>
              <details {'open' if position == 0 else ''}>
                <summary>错误</summary>
                <ul>{error_links}</ul>
              </details>
              <details {'open' if position == 0 else ''}>
                <summary>执行事件</summary>
                {event_rows}
              </details>
              <details>
                <summary>运行诊断</summary>
                <table>
                  <thead><tr><th>事件编号</th><th>文件名前缀</th><th>阶段</th><th>当前包</th><th>目标包</th><th>控件树包名</th><th>候选数量</th><th>说明</th></tr></thead>
                  <tbody>{diagnostic_rows}</tbody>
                </table>
              </details>
            </section>
            """
        )
    return "\n".join(sections)


def _case_nav(indices: list[int], events: list[dict], diagnostics: list[dict], errors: list[dict]) -> str:
    if not indices:
        return "<p>尚未记录测试用例。</p>"
    items = []
    for case_index in indices:
        event_count = sum(1 for event in events if event.get("case_index") == case_index)
        diagnostic_count = sum(1 for item in diagnostics if item.get("case_index") == case_index)
        error_count = sum(1 for err in errors if err.get("case_index") == case_index)
        items.append(
            f"<a href='#case-{html.escape(str(case_index))}'>用例 {html.escape(str(case_index))}："
            f"{event_count} 事件 / {diagnostic_count} 诊断 / {error_count} 错误</a>"
        )
    return f"<nav id='top' class='case-nav'>{''.join(items)}</nav>"


def _case_indices(events: list[dict], diagnostics: list[dict], errors: list[dict]) -> list[int]:
    indices = {
        item.get("case_index")
        for item in [*events, *diagnostics, *errors]
        if item.get("case_index") is not None
    }
    return sorted(int(index) for index in indices)


def _case_error_link(err: dict) -> str:
    title = html.escape(str(err.get("title") or f"错误 {err.get('id', '')}"))
    report_path = err.get("report_path")
    if report_path:
        return f"<li><a href='{html.escape(Path(report_path).name)}'>{title}</a></li>"
    return f"<li>{title}</li>"


def _summary_event_row(event: dict, out_dir: Path) -> str:
    action = _load_action(event)
    screenshot = event.get("screenshot_path") or ""
    image_html = ""
    if screenshot:
        image_name = _relative_or_copy_image(_highlighted_or_original(Path(screenshot)), out_dir)
        image_html = f'<img src="{html.escape(image_name)}" alt="event screenshot">'
    marker = event.get("marker") or ""
    state_hash_value = event.get("post_state_hash") or ""
    action_text = html.escape(json.dumps(action, ensure_ascii=False, indent=2))
    return f"""
    <section class="event">
      <div>{image_html}</div>
      <div>
        <h3>事件 {html.escape(str(event.get('event_index', '?')))} / {html.escape(str(event.get('kind', 'event')))}</h3>
        <p class="muted">标记：{html.escape(marker)} | 状态：{html.escape(state_hash_value)}</p>
        <pre>{action_text}</pre>
      </div>
    </section>
    """


def _diagnostic_row(item: dict) -> str:
    return f"""
    <tr>
      <td>{html.escape(str(item.get('event_index', '') or ''))}</td>
      <td>{html.escape(_event_prefix(item))}</td>
      <td>{html.escape(str(item.get('stage', '') or ''))}</td>
      <td>{html.escape(str(item.get('current_package', '') or ''))}</td>
      <td>{html.escape(str(item.get('target_package', '') or ''))}</td>
      <td>{html.escape(str(item.get('hierarchy_packages', '') or ''))}</td>
      <td>{html.escape(str(item.get('candidate_counts', '') or ''))}</td>
      <td>{html.escape(str(item.get('message', '') or ''))}</td>
    </tr>
    """


def _event_prefix(item: dict) -> str:
    case_index = item.get("case_index")
    event_index = item.get("event_index")
    if case_index is None or event_index is None:
        return ""
    return f"case-{int(case_index):03d}-event-{int(event_index):04d}"


def _relative_or_copy_image(source: Path, out_dir: Path) -> str:
    try:
        return source.relative_to(out_dir).as_posix()
    except ValueError:
        target = out_dir / source.name
        if source.exists() and source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return target.name


def _highlighted_or_original(source: Path) -> Path:
    highlighted = source.with_name(f"{source.stem}-highlight{source.suffix}")
    return highlighted if highlighted.exists() else source


def _annotated_image(source: Path, action: dict, out_dir: Path, final_error: bool) -> str:
    highlighted = source.with_name(f"{source.stem}-highlight{source.suffix}")
    target = out_dir / highlighted.name
    if highlighted.exists():
        if highlighted.resolve() != target.resolve():
            shutil.copyfile(highlighted, target)
        return target.name
    target = out_dir / f"{source.stem}-highlight.png"
    try:
        with Image.open(source) as img:
            draw = ImageDraw.Draw(img)
            width, height = img.size
            if final_error:
                pad = 8
                draw.rectangle((pad, pad, width - pad, height - pad), outline="#d93025", width=12)
            bounds = action.get("bounds")
            if isinstance(bounds, list) and len(bounds) == 4:
                draw.rectangle(tuple(bounds), outline="#1a73e8", width=8)
            elif action.get("kind") in {"back", "swipe", "restart", "permission_allow"}:
                draw.rectangle((16, 16, min(width - 16, 420), 110), outline="#1a73e8", width=8)
            img.save(target)
    except Exception:
        shutil.copyfile(source, target)
    return target.name


def _load_action(event: dict) -> dict:
    try:
        return json.loads(event.get("action_json") or "{}")
    except Exception:
        return {"raw": event.get("action_json", "")}


def _action_label(action: dict) -> str:
    kind = action.get("kind", "event")
    if kind == "back":
        return "Back"
    if kind == "swipe":
        return "Swipe"
    if kind == "restart":
        return "Restart app"
    if kind == "permission_allow":
        return "Auto allow permission"
    return kind
