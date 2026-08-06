from __future__ import annotations

import json
import sqlite3


def matching_rule_id(db: sqlite3.Connection, error_type: str, fingerprint: str, property_name: str | None = None) -> int | None:
    rows = db.execute("select * from false_positive_rules order by id").fetchall()
    for row in rows:
        rule = json.loads(row["rule_json"])
        if rule.get("error_type") and rule["error_type"] != error_type:
            continue
        if rule.get("fingerprint") and rule["fingerprint"] != fingerprint:
            continue
        if rule.get("property") and rule["property"] != property_name:
            continue
        return int(row["id"])
    return None
