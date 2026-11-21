from dataclasses import dataclass
from typing import Protocol

from .agent_context import AgentContext


@dataclass(frozen=True)
class AgentInfo:
    id: str
    name: str
    role: str


AGENTS = (
    AgentInfo("deepseek", "DeepSeek", "项目协调"),
    AgentInfo("dsh", "DSH", "勘察与执行"),
    AgentInfo("codex", "Codex", "规划与复核"),
)


@dataclass(frozen=True)
class AgentTask:
    project_id: str
    task_id: str
    session_id: str
    message: str
    context: AgentContext


class AgentAdapter(Protocol):
    def respond(self, task: AgentTask) -> str:
        """Return the user-facing result for an accepted task."""
        ...


class MockCoordinator:
    def respond(self, task: AgentTask) -> str:
        return "已记录这条补充。准备好后，可以点击「开始运行」设置工作区和验收条件。"
