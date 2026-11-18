"""A reproducible, local walkthrough with SQLite-backed checkpoints."""

from contextlib import closing
from csv import DictReader
from enum import IntEnum
from io import StringIO
import json
import sqlite3
from time import time

from .agent_context import prepare_context
from .workbench import message


class Step(IntEnum):
    REQUIREMENTS = 0
    ENVIRONMENT = 1
    PLAN = 2
    IMPLEMENT = 3
    REVIEW = 4
    REPAIR = 5
    VERIFY = 6
    DELIVERY = 7


STEPS = ("需求确认", "环境勘察", "方案拆解", "初版交付", "独立复核", "返修", "复验", "下一阶段交付")
POLICIES = {"keep": "保留原记录", "update": "更新原记录"}
FIRST_VERSION = "rev-01"
MAX_REPAIRS = 3
QUESTION = "同一编号再次导入时，遇到内容变更要保留原记录，还是按新内容覆盖？"


class DemoConflict(ValueError):
    pass


def chat(channel, author, text, **session):
    return "message.added", message(channel, author, text, **session)


def artifact(path, title, body):
    return "artifact.created", {"path": path, "title": title, "content": f"# {title}\n\n{body}\n"}


def revision(round_number):
    return f"rev-{round_number:02d}"


def simulate_import(candidate, policy, scenario):
    inserts = {
        "plain": "INSERT INTO devices VALUES (?, ?)",
        "keep": "INSERT INTO devices VALUES (?, ?) ON CONFLICT(device_id) DO NOTHING",
        "update": "INSERT INTO devices VALUES (?, ?) ON CONFLICT(device_id) DO UPDATE SET name=excluded.name",
    }
    initial = "device_id,name\nA01,网关\nB02,控制器\n"
    revised = "device_id,name\nA01,网关新版\n"
    with closing(sqlite3.connect(":memory:")) as db:
        definition = "device_id TEXT, name TEXT" if candidate == FIRST_VERSION else "device_id TEXT PRIMARY KEY, name TEXT"
        db.execute(f"CREATE TABLE devices ({definition})")
        implementation = policy
        if candidate == FIRST_VERSION:
            implementation = "plain"
        elif candidate == revision(2) and scenario == 2:
            implementation = "update" if policy == "keep" else "keep"
        insert = inserts[implementation]
        counts = []
        for content in (initial, initial, revised):
            db.executemany(insert, [(row["device_id"], row["name"]) for row in DictReader(StringIO(content))])
            counts.append(db.execute("SELECT COUNT(*) FROM devices").fetchone()[0])
        name = db.execute("SELECT name FROM devices WHERE device_id = ? ORDER BY rowid LIMIT 1", ("A01",)).fetchone()[0]
    expected_name = "网关" if policy == "keep" else "网关新版"
    return {
        "candidate": candidate, "policy": policy, "counts": counts, "name": name,
        "passed": counts == [2, 2, 2] and name == expected_name,
    }


def report(result):
    first, repeated, updated = result["counts"]
    return (
        f"- 候选版本 `{result['candidate']}`\n"
        f"- 需求策略为{POLICIES[result['policy']]}\n"
        f"- 首次导入 {first} 行，重复导入后 {repeated} 行，变更同编号后 {updated} 行\n"
        f"- A01 当前名称为「{result['name']}」\n"
        f"- 结论 `{'PASS' if result['passed'] else 'CHANGES_REQUESTED'}`\n\n"
        "验收条件是三个批次后均为 2 行，并且 A01 的名称符合所选策略。实测在本次演示的内存 SQLite 中执行。"
    )


class Demo:
    def __init__(self, store, delay=1.8, clock=time):
        self.store = store
        self.delay = delay
        self.clock = clock

    def open(self, *, fresh=False):
        with self.store.write_lock:
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if not fresh:
                    row = db.execute(
                        "SELECT p.* FROM projects p JOIN demo_runs r ON r.project_id = p.id ORDER BY p.rowid DESC LIMIT 1"
                    ).fetchone()
                    if row is not None:
                        return self.store.project_view(row)
                number = db.execute("SELECT COUNT(*) FROM demo_runs").fetchone()[0] + 1
                project_id = self.store.insert_project(db, f"CSV 导入演示 #{number}", "intake", [
                    message("deepseek", "user", "做一个 CSV 导入工具。希望同一份文件重复导入时，库里只有一份记录。"),
                    message("deepseek", "deepseek", QUESTION),
                ], demo=True)
                db.execute(
                    "INSERT INTO demo_runs (project_id, step, status, scenario) VALUES (?, ?, ?, 2)",
                    (project_id, Step.REQUIREMENTS, "waiting_input"),
                )
                self.save(db, project_id, Step.REQUIREMENTS, "waiting_input", 1, None)
            self.store.sync_files(project_id)
            return self.store.get_project(project_id)

    def save(self, db, project_id, step, status, round_number, policy):
        due = self.clock() + self.delay if status == "running" else None
        db.execute(
            "UPDATE demo_runs SET step = ?, status = ?, policy = ?, due_at = ?, round = ? WHERE project_id = ?",
            (step, status, policy, due, round_number, project_id),
        )
        self.store.append(db, project_id, "demo.changed", {
            "step": step, "status": status, "policy": policy, "round": round_number,
            "phase": STEPS[step] if step < len(STEPS) else "交付完成",
        })

    def control(self, project_id, action, policy=None):
        with self.store.write_lock:
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                run = db.execute("SELECT * FROM demo_runs WHERE project_id = ?", (project_id,)).fetchone()
                if run is None:
                    raise KeyError(project_id)
                step = run["step"]
                current = run["status"]
                if action == "confirm" and current == "waiting_input":
                    if not isinstance(policy, str) or policy not in POLICIES:
                        raise ValueError("请选择重复编号的处理方式。")
                    self.store.append(db, project_id, "message.added", message("deepseek", "user", POLICIES[policy] + "，继续演示。"))
                    self.store.append(db, project_id, "message.added", message("deepseek", "deepseek", "需求 v1 已确认，交给 DSH 勘察。"))
                    self.store.append(db, project_id, *artifact("requirements/需求定稿-v1.md", "需求定稿 v1", f"以编号识别记录。重复导入时{POLICIES[policy]}。相同文件导入两次后仍为 2 行。"))
                    self.save(db, project_id, Step.ENVIRONMENT, "running", run["round"], policy)
                elif action == "pause" and current == "running":
                    self.save(db, project_id, step, "paused", run["round"], run["policy"])
                elif action == "resume" and current == "paused":
                    self.save(db, project_id, step, "running", run["round"], run["policy"])
                else:
                    raise DemoConflict("当前状态不能执行该操作，工作台会同步最新进度。")
            self.store.sync_files(project_id)

    def tick(self):
        with self.store.write_lock:
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                run = db.execute(
                    "SELECT * FROM demo_runs WHERE status = 'running' AND due_at <= ? ORDER BY due_at, project_id LIMIT 1",
                    (self.clock(),),
                ).fetchone()
                if run is None:
                    return
                project_id = run["project_id"]
                step = Step(run["step"])
                policy = run["policy"]
                round_number = run["round"]
                if step in (Step.REVIEW, Step.VERIFY):
                    entries, next_step, status, round_number = self.review(step, policy, round_number, run["scenario"])
                else:
                    entries, blocked = self.perform(db, project_id, step, policy, round_number)
                    next_step = step if blocked else step + 1
                    status = "blocked" if blocked else "completed" if next_step == len(STEPS) else "running"
                try:
                    for kind, data in entries:
                        if kind == "message.added" and (data["author"] == "deepseek" or
                                (data["author"] == "scheduler" and data["channel"] in ("dsh", "codex"))):
                            context = prepare_context(self.store, project_id, data["channel"])
                            data["context"] = context.references()
                except (OSError, UnicodeError):
                    entries = [chat("deepseek", "scheduler", "人设或项目记忆暂时无法读取，核对文件后再继续。")]
                    next_step, status, round_number = step, "blocked", run["round"]
                for kind, data in entries:
                    self.store.append(db, project_id, kind, data)
                self.save(db, project_id, next_step, status, round_number, policy)
            self.store.sync_files(project_id)

    def perform(self, db, project_id, step, policy, round_number):
        if step == Step.ENVIRONMENT:
            return [
                chat("dsh", "scheduler", "勘察 CSV 样例、唯一键和验收环境。", session="dsh-env", title="环境勘察"),
                chat("dsh", "dsh", "样例包含设备编号和名称。已确认可用内存 SQLite 独立执行验收。", session="dsh-env", title="环境勘察"),
                artifact("environment/勘察记录.md", "环境勘察", f"SQLite `{sqlite3.sqlite_version}`。使用两条固定 CSV 记录，内存数据库在每次验收结束后释放。"),
                chat("deepseek", "scheduler", "勘察已归档，将需求 v1 与环境记录交给 Codex 规划。"),
            ], False
        if step == Step.PLAN:
            return [
                chat("codex", "scheduler", "用需求 v1 和勘察结果制定阶段方案。", session="codex-plan", title="总体规划"),
                chat("codex", "codex", "阶段 1 实现导入，通过重复导入和同编号变更验收后再进入阶段 2，整理交付包。", session="codex-plan", title="总体规划"),
                artifact("plan/总体方案.md", "方案 v1", "1. 实现 CSV 导入，独立验证重复导入与冲突策略。\n2. 汇总已通过复验的候选与证据。"),
                artifact("stages/阶段1-实现与验收.md", "阶段 1 · 实现与验收", "导入两条记录，重复导入同一文件，再提交一个相同编号的名称变更。各批次后均保留两条记录。"),
                artifact("stages/阶段2-整理交付.md", "阶段 2 · 整理交付", "检查通过复验的候选和测试证据引用，生成交付摘要。"),
                chat("deepseek", "scheduler", "方案已归档。现将阶段 1 和验收条件发给 DSH。"),
            ], False
        if step in (Step.IMPLEMENT, Step.REPAIR):
            repair = step == Step.REPAIR
            candidate = revision(round_number)
            session = {
                "session": f"dsh-{round_number}", "title": f"第 {round_number - 1} 轮返修" if repair else "初版交付",
                "candidate": candidate,
            }
            task = "根据上一轮复核意见返修，提交新版本独立复验。" if repair else "实现 CSV 导入，交付初版候选。"
            reply = f"已提交 {candidate}。" + ("已调整导入处理，等待独立复验。" if repair else "先实现基本导入，等待独立复核。")
            return [chat("dsh", "scheduler", task, **session), chat("dsh", "dsh", reply, **session)], False
        return self.deliver(db, project_id, policy, revision(round_number))

    @staticmethod
    def review(step, policy, round_number, scenario):
        candidate = revision(round_number)
        result = simulate_import(candidate, policy, scenario)
        if result["candidate"] != candidate or result["policy"] != policy:
            return [chat("deepseek", "scheduler", "本轮复核证据与候选版本或需求策略不一致，已停止推进。")], step, "blocked", round_number
        session = {"session": f"codex-{round_number}", "title": f"第 {round_number} 轮复核", "candidate": candidate}
        entries = [
            chat("codex", "scheduler", f"复核 {candidate} 的实际导入结果。", **session),
            ("test.completed", result),
            artifact(f"reviews/{candidate}.md", f"复核结果 {candidate}", report(result)),
        ]
        if result["passed"]:
            entries += [
                chat("codex", "codex", "PASS。三个批次均保留 2 行，名称符合已确认的策略。", **session),
                ("stage.accepted", {"title": "阶段 1 导入实现", "candidate": candidate}),
                chat("deepseek", "scheduler", f"{candidate} 已通过复验，自动进入阶段 2 整理交付。"),
            ]
            return entries, Step.DELIVERY, "running", round_number

        if result["counts"] != [2, 2, 2]:
            finding = f"F-01 三批导入后的行数为 {result['counts']}，期望各为 2 行。"
        else:
            finding = f"F-02 行数已正确，A01 的名称为「{result['name']}」，仍不符合「{POLICIES[policy]}」策略。"
        entries.append(chat("codex", "codex", finding, **session))
        if round_number > MAX_REPAIRS:
            entries.append(chat("deepseek", "scheduler", f"已达到 {MAX_REPAIRS} 次返修上限。验收阻塞，历轮证据已保留。"))
            return entries, step, "blocked", round_number
        entries.append(chat("deepseek", "scheduler", f"{candidate} 未通过复核，已将问题回派 DSH。下一候选为 {revision(round_number + 1)}。"))
        return entries, Step.REPAIR, "running", round_number + 1

    @staticmethod
    def deliver(db, project_id, policy, candidate):
        row = db.execute(
            "SELECT data FROM events WHERE project_id = ? AND kind = 'test.completed' ORDER BY seq DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        result = json.loads(row["data"]) if row else {}
        if not (result.get("passed") and result.get("candidate") == candidate and result.get("policy") == policy):
            return [chat("deepseek", "scheduler", "缺少与本次候选版本绑定的通过证据，交付已阻塞。")], True
        return [
            chat("dsh", "scheduler", "整理已通过复验的候选版本、需求与测试证据。", session="dsh-delivery", title="阶段 2 交付", candidate=candidate),
            chat("dsh", "dsh", "交付摘要已生成，关联需求、方案、审核和验收结果。", session="dsh-delivery", title="阶段 2 交付", candidate=candidate),
            artifact("evidence/交付摘要.md", "CSV 导入演示交付摘要", f"需求版本 v1，候选 `{candidate}`。\n\n{report(result)}\n\n复核记录位于 `reviews/{candidate}.md`。"),
            ("stage.accepted", {"title": "阶段 2 交付整理", "candidate": candidate}),
            chat("deepseek", "deepseek", "演示完成。打开交付物可查看历轮复核和最终交付摘要。"),
        ], False
