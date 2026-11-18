from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .project_memory import checkpoint, merge_memory


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "workbench.sqlite3"
        self.write_lock = RLock()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, stage TEXT NOT NULL,
                    demo INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, occurred_at TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_project_seq ON events(project_id, seq);
                CREATE TABLE IF NOT EXISTS demo_runs (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id),
                    step INTEGER NOT NULL, status TEXT NOT NULL,
                    policy TEXT, due_at REAL,
                    round INTEGER NOT NULL DEFAULT 1,
                    scenario INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS agent_connections (
                    agent_id TEXT PRIMARY KEY, settings TEXT NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(demo_runs)")}
            if "round" not in columns:
                db.execute("ALTER TABLE demo_runs ADD COLUMN round INTEGER NOT NULL DEFAULT 1")
                db.execute("UPDATE demo_runs SET round = 2 WHERE step >= 5")
            if "scenario" not in columns:
                db.execute("ALTER TABLE demo_runs ADD COLUMN scenario INTEGER NOT NULL DEFAULT 1")
        for project in self.list_projects():
            self.sync_files(project["id"])

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    @staticmethod
    def project_view(row):
        project = dict(row)
        project["demo"] = bool(project["demo"])
        return project

    def list_projects(self):
        with self.connection() as db:
            return [self.project_view(row) for row in db.execute(
                "SELECT * FROM projects ORDER BY rowid DESC"
            )]

    def get_project(self, project_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise KeyError(project_id)
            return self.project_view(row)

    @staticmethod
    def append(db, project_id, kind, data):
        db.execute(
            "INSERT INTO events (project_id, kind, occurred_at, data) VALUES (?, ?, ?, ?)",
            (project_id, kind, timestamp(), json.dumps(data, ensure_ascii=False)),
        )

    def insert_project(self, db, name, stage, messages, *, demo=False):
        project_id = uuid4().hex
        db.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            (project_id, name, stage, int(demo), timestamp()),
        )
        self.append(db, project_id, "project.created", {"stage": stage})
        for message in messages:
            self.append(db, project_id, "message.added", message)
        return project_id

    def create_project(self, name, stage, messages):
        with self.write_lock:
            with self.connection() as db:
                project_id = self.insert_project(db, name, stage, messages)
            self.sync_files(project_id)
            return self.get_project(project_id)

    def add_messages(self, project_id, messages):
        with self.write_lock:
            self.get_project(project_id)
            with self.connection() as db:
                for message in messages:
                    self.append(db, project_id, "message.added", message)
            self.sync_files(project_id)

    def events(self, project_id, after=0, limit=200):
        with self.connection() as db:
            rows = db.execute(
                "SELECT seq, kind, occurred_at, data FROM events "
                "WHERE project_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (project_id, after, limit),
            )
            return [dict(row) | {"data": json.loads(row["data"])} for row in rows]

    def memory_path(self, project_id):
        path = self.root / "projects" / project_id / "MEMORY.md"
        if path.resolve() != path:
            raise OSError("The project memory path must stay within its project directory.")
        return path

    def read_memory(self, project_id):
        self.get_project(project_id)
        path = self.memory_path(project_id)
        with self.write_lock:
            if not path.exists():
                self.sync_files(project_id)
            return {"path": str(path), "content": path.read_text(encoding="utf-8-sig")}

    def sync_files(self, project_id):
        """Rebuild the owned Markdown projection from committed input events."""
        try:
            project = self.get_project(project_id)
            folder = self.root / "projects" / project_id
            for subdirectory in ("requirements", "environment", "plan", "stages", "reviews", "evidence", "workspace"):
                (folder / subdirectory).mkdir(parents=True, exist_ok=True)
            with self.connection() as db:
                rows = db.execute(
                    "SELECT kind, occurred_at, data FROM events WHERE project_id = ? "
                    "AND kind IN ('message.added', 'artifact.created', 'stage.accepted', 'demo.changed', 'test.completed') ORDER BY seq",
                    (project_id,),
                )
                entries = [dict(row) | {"data": json.loads(row["data"])} for row in rows]
            lines = [f"# {project['name']} · 需求档案", "", "由工作台按聊天记录生成。", ""]
            progress = ["# 实施进度与续接", ""]
            for entry in entries:
                data = entry["data"]
                if entry["kind"] == "message.added" and data["author"] == "user" and data["channel"] == "deepseek":
                    lines.extend([data["text"], "", "---", ""])
                elif entry["kind"] == "artifact.created":
                    target = (folder / data["path"]).resolve()
                    if target.is_relative_to(folder.resolve()):
                        self.write_file(target, data["content"])
                elif entry["kind"] == "stage.accepted":
                    date = datetime.fromisoformat(entry["occurred_at"]).astimezone().isoformat(timespec="seconds")
                    progress.append(f"- {date}－{data['title']}已完成（演示，{data['candidate']}）")
            self.write_file(folder / "requirements" / "需求档案.md", "\n".join(lines))
            progress_path = folder / "progress.md"
            if len(progress) > 2 or not progress_path.exists():
                if len(progress) == 2:
                    progress.append("当前无正式验收记录。")
                self.write_file(progress_path, "\n".join(progress) + "\n")
            memory = self.memory_path(project_id)
            current = memory.read_text(encoding="utf-8-sig") if memory.exists() else ""
            merged = merge_memory(current, checkpoint(project, entries))
            if merged != current:
                self.write_file(memory, merged)
        except (OSError, UnicodeError):
            logging.exception("Project %s Markdown export failed; retry on next input or startup", project_id)

    @staticmethod
    def write_file(path, content):
        pending = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            pending.write_text(content, encoding="utf-8")
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)
