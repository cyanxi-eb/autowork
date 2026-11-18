from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


PERSONAS = Path(__file__).resolve().parent / "personas"
AGENT_IDS = frozenset(("deepseek", "dsh", "codex"))
CONTEXT_LIMIT = 6000
TRUNCATED = "\n\n[中段已省略，完整内容见原文件]\n\n"


def persona_path(agent_id):
    if agent_id not in AGENT_IDS:
        raise KeyError(agent_id)
    return PERSONAS / agent_id / "SOUL.md"


def bounded(text):
    if len(text) <= CONTEXT_LIMIT:
        return text
    half = (CONTEXT_LIMIT - len(TRUNCATED)) // 2
    return text[:half] + TRUNCATED + text[-half:]


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    project_id: str
    persona: str
    project_memory: str

    def references(self):
        return {
            "persona_file": f"orchestrator/personas/{self.agent_id}/SOUL.md",
            "persona_sha256": sha256(self.persona.encode("utf-8")).hexdigest(),
            "memory_file": "MEMORY.md",
            "memory_sha256": sha256(self.project_memory.encode("utf-8")).hexdigest(),
            "project_id": self.project_id,
        }


def prepare_context(store, project_id, agent_id):
    return AgentContext(
        agent_id, project_id, bounded(persona_path(agent_id).read_text(encoding="utf-8-sig")),
        bounded(store.read_memory(project_id)["content"]),
    )
