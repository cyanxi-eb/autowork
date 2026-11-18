from dataclasses import asdict
from uuid import uuid4

from .agent_context import prepare_context
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
        context = prepare_context(self.store, project_id, "deepseek")
        task = AgentTask(project_id, uuid4().hex, "main", text, context)
        self.store.add_messages(project_id, [
            message("deepseek", "user", text),
            message("deepseek", "deepseek", self.coordinator.respond(task)) | {"context": context.references()},
        ])
