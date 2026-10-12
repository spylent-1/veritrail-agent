"""SQLite is the source of truth; JSONL is a derived export."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, Message, RunReport
from repopilot.sanitize import clean_data


def now() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: object) -> str:
    return json.dumps(clean_data(value), ensure_ascii=False)


class SessionStore:
    def __init__(self, home: Path) -> None:
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(home / "sessions.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.db.close()
            raise RepoPilotError("SCHEMA_VERSION", "Database belongs to a newer RepoPilot version.")
        if version == 0:
            self.db.executescript("""
                BEGIN;
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    mode TEXT NOT NULL, config TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, report TEXT
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    run_id TEXT NOT NULL REFERENCES runs(id), payload TEXT NOT NULL
                );
                CREATE TABLE events (
                    seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE tool_executions (
                    run_id TEXT NOT NULL REFERENCES runs(id), call_id TEXT NOT NULL,
                    name TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    intent TEXT, result TEXT, PRIMARY KEY (run_id, call_id)
                );
                PRAGMA user_version = 1;
                COMMIT;
            """)

    def close(self) -> None:
        self.db.close()

    def create_session(self, workspace: Path, title: str) -> str:
        identifier = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (identifier, str(workspace.resolve()), encode(title[:100]), now()),
            )
        return identifier

    def start_run(self, session_id: str, mode: str, config: JsonObject) -> str:
        identifier = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (identifier, session_id, mode, encode(config), "running", now()),
            )
        return identifier

    def record(self, run_id: str, kind: str, payload: JsonObject) -> None:
        data = clean_data(payload)
        with self.db:
            self.db.execute(
                "INSERT INTO events(run_id,kind,created_at,payload) VALUES (?,?,?,?)",
                (run_id, kind, now(), encode(data)),
            )
            if kind == "message":
                row = self.run(run_id)
                self.db.execute(
                    "INSERT INTO messages(session_id,run_id,payload) VALUES (?,?,?)",
                    (row["session_id"], run_id, encode(data)),
                )
                for call in Message.model_validate(data).tool_calls:
                    self.db.execute(
                        "INSERT INTO tool_executions VALUES (?,?,?,?,?,NULL,NULL)",
                        (run_id, call.id, call.name, call.arguments, "proposed"),
                    )
            elif kind == "tool_started":
                self.db.execute(
                    "UPDATE tool_executions SET status='running' WHERE run_id=? AND call_id=?",
                    (run_id, data["call_id"]),
                )
            elif kind == "tool_finished":
                self.db.execute(
                    "UPDATE tool_executions SET status=?,result=? WHERE run_id=? AND call_id=?",
                    (data["status"], encode(data), run_id, data["call_id"]),
                )
            elif kind == "tool_intent":
                self.db.execute(
                    "UPDATE tool_executions SET intent=? WHERE run_id=? AND call_id=?",
                    (encode(data["intent"]), run_id, data["call_id"]),
                )
            elif kind == "run_finished":
                self.db.execute(
                    "UPDATE runs SET status=?,report=? WHERE id=?",
                    (data["status"], encode(data), run_id),
                )

    def run(self, run_id: str) -> JsonObject:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RepoPilotError("RUN_NOT_FOUND", "No such run in the host state directory.")
        return dict(row)

    def session(self, session_id: str) -> JsonObject:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise RepoPilotError(
                "SESSION_NOT_FOUND", "No such session in the host state directory."
            )
        return dict(row)

    def sessions(self) -> list[JsonObject]:
        return [
            dict(row) for row in self.db.execute("SELECT * FROM sessions ORDER BY created_at DESC")
        ]

    def messages(self, session_id: str) -> list[Message]:
        return [
            Message.model_validate_json(row[0])
            for row in self.db.execute(
                "SELECT payload FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            )
        ]

    def events(self, run_id: str) -> list[JsonObject]:
        self.run(run_id)
        return [
            {
                "seq": row["seq"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "data": json.loads(row["payload"]),
            }
            for row in self.db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,)
            )
        ]

    def report(self, run_id: str) -> RunReport | None:
        payload = self.run(run_id)["report"]
        return RunReport.model_validate_json(payload) if payload else None

    def runs(self, session_id: str) -> list[JsonObject]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM runs WHERE session_id=? ORDER BY rowid", (session_id,)
            )
        ]

    def tools(self, run_id: str) -> list[JsonObject]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM tool_executions WHERE run_id=? ORDER BY rowid", (run_id,)
            )
        ]

    def host_tool(self, run_id: str, name: str, call_id: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO tool_executions VALUES (?,?,?,?,?,NULL,NULL)",
                (run_id, call_id, name, "{}", "proposed"),
            )
        self.record(run_id, "host_tool", {"name": name, "call_id": call_id})
