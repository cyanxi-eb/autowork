from dataclasses import asdict
from uuid import uuid4

from .agents import AGENTS, AgentAdapter, AgentTask


def message(channel, author, text, *, session="main", title="项目对话", candidate=None):
    return {
        "channel": channel, "author": author, "text": text, "session_id": session,
        "session_title": title, "candidate": candidate,
    }


class Workbench:
    def __init__(self, store, coordinator: AgentAdapter):
        self.store = store
        self.coordinator = coordinator

    @staticmethod
    def agents():
        return [asdict(agent) for agent in AGENTS]

    def create(self, name, request):
        messages = [
            message("deepseek", "user", request),
            message("deepseek", "deepseek", "项目已建档。你可以在这里继续补充交付物、边界和验收条件。"),
        ]
        return self.store.create_project(name, "intake", messages)

    def add_input(self, project_id, text):
        self.store.get_project(project_id)
        task = AgentTask(project_id, uuid4().hex, "main", text)
        self.store.add_messages(project_id, [
            message("deepseek", "user", text),
            message("deepseek", "deepseek", self.coordinator.respond(task)),
        ])

    def demo(self):
        return self.store.create_project("CSV 导入工具", "review", [
            message("deepseek", "user", "做一个工具，把 CSV 导入本地数据库。重复导入同一份文件时只保留一份记录。"),
            message("deepseek", "deepseek", "已明确验收条件。这个示例展示需求建档、DSH 实施、Codex 复核和返修的交接。"),
            message("dsh", "scheduler", "实现 CSV 导入，并记录重复导入测试的结果。", session="dsh-1", title="第 1 轮 · 初版交付", candidate="rev-01"),
            message("dsh", "dsh", "初版已交付，首次导入测试通过。", session="dsh-1", title="第 1 轮 · 初版交付", candidate="rev-01"),
            message("codex", "scheduler", "复核 rev-01，重点检查重复导入行为。", session="codex-1", title="第 1 轮 · 初版复核", candidate="rev-01"),
            message("codex", "codex", "需返修。相同文件导入两次会增加重复记录。请添加重复导入验收项。", session="codex-1", title="第 1 轮 · 初版复核", candidate="rev-01"),
            message("deepseek", "scheduler", "Codex 对 rev-01 提出返修意见。已将重复导入问题交给 DSH，修订版待独立复核。"),
            message("dsh", "scheduler", "返修重复导入问题，提交新版本和两次导入的测试结果。", session="dsh-2", title="第 2 轮 · 返修交付", candidate="rev-02"),
            message("dsh", "dsh", "已提交 rev-02。两次导入后记录数一致，等待 Codex 复核。", session="dsh-2", title="第 2 轮 · 返修交付", candidate="rev-02"),
        ], demo=True)
