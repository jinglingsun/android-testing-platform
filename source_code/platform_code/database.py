from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import DATA_DIR


DB_PATH = DATA_DIR / "platform.sqlite3"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout=1000")
    return conn


def init_db() -> None:
    with connect() as db:
        db.execute("pragma journal_mode=wal")
        db.executescript(
            """
            create table if not exists tasks (
                id integer primary key autoincrement,
                device_id text not null,
                apk_path text not null,
                package_name text,
                status text not null,
                config_json text not null,
                run_dir text not null,
                started_at text not null,
                finished_at text
            );

            create table if not exists events (
                id integer primary key autoincrement,
                task_id integer not null,
                case_index integer not null,
                event_index integer not null,
                kind text not null,
                action_json text not null,
                pre_state_hash text,
                post_state_hash text,
                screenshot_path text,
                hierarchy_path text,
                marker text,
                created_at text not null
            );

            create table if not exists errors (
                id integer primary key autoincrement,
                task_id integer not null,
                case_index integer not null,
                event_id integer,
                error_type text not null,
                fingerprint text not null,
                title text not null,
                detail text not null,
                report_path text,
                suppressed integer not null default 0,
                false_positive_rule_id integer,
                created_at text not null
            );

            create table if not exists false_positive_rules (
                id integer primary key autoincrement,
                name text not null,
                rule_json text not null,
                created_at text not null
            );

            create table if not exists replays (
                id integer primary key autoincrement,
                error_id integer not null,
                task_id integer not null,
                status text not null,
                detail text,
                started_at text not null,
                finished_at text
            );

            create table if not exists diagnostics (
                id integer primary key autoincrement,
                task_id integer not null,
                case_index integer,
                event_index integer,
                stage text not null,
                current_package text,
                target_package text,
                state_hash text,
                hierarchy_packages text,
                candidate_counts text,
                selected_action text,
                message text,
                created_at text not null
            );
            """
        )
        _migrate(db)


def _migrate(db: sqlite3.Connection) -> None:
    tables = {
        row["name"] for row in db.execute("select name from sqlite_master where type='table'")
    }
    if "errors" in tables:
        _add_column_if_missing(db, "errors", "suppressed", "integer not null default 0")
        _add_column_if_missing(db, "errors", "false_positive_rule_id", "integer")
    if "tasks" in tables:
        _add_column_if_missing(db, "tasks", "finished_at", "text")
    db.commit()


def _add_column_if_missing(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in db.execute(f"pragma table_info({table})")}
    if column not in columns:
        db.execute(f"alter table {table} add column {column} {definition}")


def insert(db: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = db.execute(
        f"insert into {table} ({columns}) values ({placeholders})",
        list(values.values()),
    )
    db.commit()
    return int(cursor.lastrowid)
