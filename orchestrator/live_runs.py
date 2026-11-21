"""Single-stage dispatch with durable progress and isolated Git candidates."""

from contextlib import contextmanager
import json
import logging
import os
from threading import Thread
from uuid import uuid4

from .agent_context import prepare_context
from .agent_protocol import AgentResult, ProtocolError, VERSION
from .agent_runtimes import AgentError
from .candidate import Candidate, source_snapshot
from .demo import MAX_REPAIRS
from .workbench import message


PHASES = {
    "coordinate": ("deepseek", "需求确认", "汇总任务范围和验收。只有缺少必要信息时才以 needs_input 提问。不要扩大用户确认的验收范围。"),
    "survey": ("dsh", "环境勘察", "只读检查仓库，找出相关文件、已有约定和测试入口。"),
    "plan": ("codex", "方案拆解", "只读制定小范围实施方案和独立验证方法。"),
    "implement": ("dsh", "初版实施", "仅在候选副本修改文件。优先用文件工具和相对路径编辑，终端访问受限时也使用文件工具。"
                  "运行能执行的相关检查并报告结果。保持改动未提交，AutoWork 会记录提交。"),
    "review": ("codex", "独立复核", "独立检查 baseline 到 candidate 的 Git 差异并执行可行的验收检查。逐项报告证据。"
               "只有亲自验证全部必验项时才给 pass。可修复问题给 changes_requested，无法验证给 blocked。"),
    "repair": ("dsh", "返修", "按最新复核发现逐项修复并验证。优先用文件工具和相对路径编辑。"
               "保持改动未提交，AutoWork 会记录新候选。"),
}
NEXT = {"coordinate": "survey", "survey": "plan", "plan": "implement", "implement": "review", "repair": "review"}
ACTIVE = {"queued", "running", "waiting_input"}


class RunConflict(ValueError):
    pass


@contextmanager
def exclusive_run(path):
    """A per-run OS lock distinguishes a crashed owner from another server."""
    with path.open("a+b") as file:
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError, OSError):
            yield False
        else:
            # Closing the handle releases the lock, including after a crash.
            yield True


class LiveRuns:
    def __init__(self, store, connections, runtimes):
        self.store = store
        self.connections = connections
        self.runtimes = runtimes
        self.worker = None

    def start(self, project_id, source, acceptance, *, local_coordinator):
        project = self.store.get_project(project_id)
        if project["demo"]:
            raise RunConflict("演示项目使用独立的演示流程。")
        source, base = source_snapshot(source)
        connections = {agent: self.connections.get(agent) for agent in ("deepseek", "dsh", "codex")}
        missing = [agent for agent, info in connections.items()
                   if not info["available"] and (agent != "deepseek" or not local_coordinator)]
        if missing:
            raise ValueError("请先配置以下 Agent 的凭据或命令：" + "、".join(missing))
        with self.store.write_lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.latest(db, project_id)
            if previous and previous["status"] in ACTIVE:
                raise RunConflict("此项目已有未完成的运行。")
            run = {
                "id": uuid4().hex, "project_id": project_id, "source": source, "base": base, "acceptance": acceptance,
                "coordinate_locally": local_coordinator, "settings": {agent: info["settings"] for agent, info in connections.items()},
                "phase": "coordinate", "status": "queued", "round": 1, "candidate": None, "task_id": None,
                "sessions": {}, "results": {}, "history": [], "detail": "已记录待派发任务。",
            }
            db.execute("INSERT INTO live_runs (id, project_id, status, state) VALUES (?, ?, ?, ?)",
                       (run["id"], project_id, run["status"], self.encode(run)))
            self.changed(db, run)
        return self.view(run)

    def add_input(self, project_id, text):
        with self.store.write_lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.latest(db, project_id)
            if not run or run["status"] not in ACTIVE:
                return False
            if run["status"] != "waiting_input":
                raise RunConflict("运行中的验收条件已固定，请等本轮完成后补充。")
            self.store.append(db, project_id, "message.added", message("deepseek", "user", text))
            self.save(db, run, "queued", "已收到补充，继续确认需求。")
        self.store.sync_files(project_id)
        return True

    def cancel_waiting(self, project_id):
        with self.store.write_lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.latest(db, project_id)
            if not run or run["status"] != "waiting_input":
                raise RunConflict("只有等待补充的运行可以结束。")
            self.save(db, run, "blocked", "已结束此次需求确认。")
        self.store.sync_files(project_id)
        return self.view(run)

    def retry_review(self, project_id, acceptance=None):
        self.store.get_project(project_id)
        connection = self.connections.get("codex")
        if not connection["available"]:
            raise ValueError("请先配置 Codex CLI。")
        with self.store.write_lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.latest(db, project_id)
            if not run or run["status"] not in ("interrupted", "blocked") or run["phase"] != "review" or not run["candidate"]:
                raise RunConflict("只有已固定候选且停止的复核可以重试。")
            if acceptance and acceptance != run["acceptance"]:
                self.store.append(db, project_id, "review.acceptance_changed", {
                    "run_id": run["id"], "candidate": run["candidate"], "before": run["acceptance"], "after": acceptance,
                })
                run["acceptance"] = acceptance
            run["settings"]["codex"] = connection["settings"]
            run["sessions"].pop("review", None)
            self.save(db, run, "queued", "已排入一次只读复核，继续使用原候选提交。")
        return self.view(run)

    @staticmethod
    def latest(db, project_id):
        row = db.execute("SELECT state FROM live_runs WHERE project_id = ? ORDER BY rowid DESC LIMIT 1", (project_id,)).fetchone()
        return json.loads(row["state"]) if row else None

    @staticmethod
    def encode(run):
        return json.dumps(run, ensure_ascii=False)

    @staticmethod
    def view(run):
        return {key: run[key] for key in ("id", "base", "source", "acceptance", "status", "phase", "round", "candidate", "detail")}

    def changed(self, db, run):
        self.store.append(db, run["project_id"], "live.changed", self.view(run))

    def save(self, db, run, status, detail):
        run.update(status=status, detail=detail)
        db.execute("UPDATE live_runs SET status = ?, state = ? WHERE id = ?", (status, self.encode(run), run["id"]))
        self.changed(db, run)

    def tick(self):
        if self.worker and self.worker.is_alive():
            return
        with self.store.connection() as db:
            rows = db.execute("SELECT id FROM live_runs WHERE status IN ('queued', 'running') ORDER BY status, rowid").fetchall()
        if rows:
            self.worker = Thread(target=self.advance, args=([row["id"] for row in rows],), daemon=True)
            self.worker.start()

    def advance(self, run_ids):
        for run_id in run_ids:
            folder = self.store.root / "runs" / run_id
            try:
                folder.mkdir(parents=True, exist_ok=True)
                with exclusive_run(folder / "worker.lock") as acquired:
                    if acquired and self.advance_owned(run_id, folder):
                        return
            except OSError:
                logging.exception("Cannot access working directory for run %s", run_id)

    def advance_owned(self, run_id, folder):
        with self.store.write_lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM live_runs WHERE id = ?", (run_id,)).fetchone()
            run = json.loads(row["state"])
            if run["status"] == "running":
                detail = "上次执行结果待核实。候选副本已保留，请检查后再从源仓库启动新运行。"
                if run["phase"] == "review" and run["candidate"]:
                    detail = "上次复核结果待核实。候选提交已保留，可以发起一次新的只读复核。"
                self.save(db, run, "interrupted", detail)
            elif run["status"] == "queued":
                run["task_id"] = uuid4().hex
                self.save(db, run, "running", f"{PHASES[run['phase']][1]}正在执行。")
            else:
                return False
        if run["status"] == "running":
            self.execute(run, folder)
        self.store.sync_files(run["project_id"])
        return True

    def execute(self, run, folder):
        agent, title, instructions = PHASES[run["phase"]]
        project_id = run["project_id"]
        try:
            task = self.task(run, instructions)
            if run["phase"] == "coordinate" and run["coordinate_locally"]:
                result = AgentResult("ready", "按用户确认的需求和验收条件开始。", [])
                self.finish(run, "scheduler", title, task, result, None, folder)
                return
            checkout = Candidate(folder, run)
            if run["phase"] == "survey":
                checkout.prepare()
            if agent != "deepseek":
                checkout.frozen(run["candidate"] or run["base"])
            session_key = self.session_key(run["phase"])
            session = run["sessions"].get(session_key)
            self.dispatch(run, agent, title, task, bool(session))
            result, native_session = self.runtimes.run(
                agent, task, checkout.path if agent != "deepseek" else None,
                session, run["settings"][agent], run["history"],
            )
            if agent != "deepseek":
                if run["phase"] in ("implement", "repair") and result.status == "done":
                    run["candidate"] = checkout.capture()
                elif run["phase"] not in ("implement", "repair"):
                    checkout.frozen(run["candidate"] or run["base"])
            self.finish(run, agent, title, task, result, native_session, folder)
        except (AgentError, ProtocolError, OSError, UnicodeError) as error:
            detail = str(error) if isinstance(error, (AgentError, ProtocolError)) else "无法读取项目档案或候选副本。"
            with self.store.write_lock, self.store.connection() as db:
                self.save(db, run, "blocked", f"{title}：{detail}")
                self.store.append(db, project_id, "message.added", message(agent, "scheduler", detail, **self.message_scope(run, title)))
        except Exception:
            logging.exception("Run %s failed unexpectedly", run["id"])
            with self.store.write_lock, self.store.connection() as db:
                self.save(db, run, "blocked", f"{title}出现内部错误，请检查本地日志。")

    @staticmethod
    def session_key(phase):
        return "implementation" if phase in ("implement", "repair") else phase

    @staticmethod
    def message_scope(run, title):
        return {
            "session": f"{run['id']}-{run['phase']}-{run['round']}",
            "title": f"{title} · 第 {run['round']} 轮",
            "candidate": run["candidate"],
        }

    def task(self, run, instructions):
        agent = PHASES[run["phase"]][0]
        context = prepare_context(self.store, run["project_id"], agent)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT data FROM events WHERE project_id = ? AND kind = 'message.added' ORDER BY seq",
                (run["project_id"],),
            )
            requests = [item["text"] for row in rows if (item := json.loads(row["data"]))["author"] == "user"]
        return {
            "version": VERSION, "task_id": run["task_id"], "project_id": run["project_id"], "run_id": run["id"],
            "phase": run["phase"], "round": run["round"], "baseline": run["base"], "candidate": run["candidate"],
            "instructions": instructions, "request": "\n\n".join(requests[-6:])[-14000:],
            "acceptance": run["acceptance"], "previous": run["results"],
            "context": {"persona": context.persona, "project_memory": context.project_memory},
        }

    def dispatch(self, run, agent, title, task, resumed):
        with self.store.write_lock, self.store.connection() as db:
            self.store.append(db, run["project_id"], "task.dispatched", {
                "run_id": run["id"], "task_id": task["task_id"], "agent": agent, "phase": run["phase"],
                "round": run["round"], "session_reused": resumed, "candidate": run["candidate"],
            })
            self.store.append(db, run["project_id"], "message.added", message(
                agent, "scheduler", task["instructions"] + ("（续接原会话）" if resumed else ""), **self.message_scope(run, title),
            ))

    def finish(self, run, agent, title, task, result, native_session, folder):
        phase, project = run["phase"], run["project_id"]
        if native_session:
            run["sessions"][self.session_key(phase)] = native_session
        run["results"][phase] = {"summary": result.summary, "items": result.items}
        body = "\n".join([result.summary, "", *(f"- {item}" for item in result.items)])
        document = f"runs/{run['id']}/{phase}-{run['round']}-{run['task_id'][:6]}.md"
        report = ""
        if phase == "review" and result.status == "pass":
            checkout = Candidate(folder, run)
            report = (
                f"- 基线提交 `{run['base']}`\n- 通过提交 `{run['candidate']}`\n"
                f"- 候选副本 `{checkout.path}`\n\n## 验收结果\n\n{body}\n\n"
                f"## 文件变更\n\n```text\n{checkout.changed_files(run['candidate'])}\n```\n"
            )
        with self.store.write_lock, self.store.connection() as db:
            self.store.append(db, project, "task.result", {
                "run_id": run["id"], "task_id": task["task_id"], "agent": agent, "phase": phase, "round": run["round"],
                "status": result.status, "candidate": run["candidate"], "session_id": native_session,
            })
            channel = "deepseek" if agent == "scheduler" else agent
            self.store.append(db, project, "message.added", message(channel, agent, body, **self.message_scope(run, title)))
            self.artifact(db, run, document, f"{title} · 第 {run['round']} 轮", body)
            if phase == "coordinate" and result.status == "needs_input":
                run["history"] = (run["history"] + [
                    {"role": "user", "content": task["request"][-4000:]},
                    {"role": "assistant", "content": body[:4000]},
                ])[-8:]
                self.save(db, run, "waiting_input", "请在中央对话中补充需求。")
            elif result.status == "blocked":
                self.save(db, run, "blocked", result.summary)
            elif phase == "review" and result.status == "changes_requested":
                if run["round"] > MAX_REPAIRS:
                    self.save(db, run, "blocked", f"已达到 {MAX_REPAIRS} 次返修上限。")
                else:
                    run.update(phase="repair", round=run["round"] + 1)
                    self.save(db, run, "queued", "复核提出返修，已排入下一轮。")
            elif phase == "review":
                self.artifact(db, run, f"runs/{run['id']}/delivery.md", "交付摘要", report)
                self.store.append(db, project, "stage.accepted", {
                    "title": "独立复核", "candidate": run["candidate"], "run_id": run["id"],
                })
                self.store.append(db, project, "message.added", message(
                    "deepseek", "scheduler", f"候选 {run['candidate'][:12]} 已通过独立复核，可打开交付物查看结果。"
                ))
                self.save(db, run, "completed", "独立复核通过，候选副本和交付记录已保留。")
            else:
                run["phase"] = NEXT[phase]
                self.save(db, run, "queued", f"{title}完成，等待下一阶段。")

    def artifact(self, db, run, path, title, body):
        self.store.append(db, run["project_id"], "artifact.created", {
            "path": path, "title": title, "content": f"# {title}\n\n{body}\n",
        })
