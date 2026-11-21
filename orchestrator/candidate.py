"""Git snapshots for a single isolated AutoWork run."""

import os
from pathlib import Path
import subprocess

from .agent_runtimes import AgentError


def git(cwd, *args, limit=1_000_000):
    try:
        process = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, *args],
            cwd=cwd, capture_output=True, timeout=90,
            env=os.environ | {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_LFS_SKIP_SMUDGE": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise AgentError("无法运行 Git，请核对安装和工作区。") from error
    if process.returncode:
        raise AgentError(f"Git {args[0]} 未成功（退出码 {process.returncode}）。")
    if len(process.stdout) > limit:
        raise AgentError(f"Git {args[0]} 输出过大。")
    return process.stdout.decode("utf-8", errors="replace").strip()


def source_snapshot(path):
    source = Path(path).expanduser().resolve()
    if not source.is_dir() or not (source / ".git").exists():
        raise ValueError("请选择包含 .git 的本地仓库根目录。")
    try:
        return str(source), git(source, "rev-parse", "--verify", "HEAD")
    except AgentError as error:
        raise ValueError("目标仓库需要至少一个可读取的 Git 提交。") from error


class Candidate:
    def __init__(self, folder, run):
        self.path = folder / "checkout"
        self.run = run

    def prepare(self):
        source, base = self.run["source"], self.run["base"]
        git(self.path.parent, "clone", "--no-local", "--no-tags", "--no-checkout", "--", source, str(self.path))
        git(self.path, "remote", "remove", "origin")
        git(self.path, "switch", "--detach", base)
        self.frozen(base)

    def frozen(self, commit):
        self.at(commit)
        if git(self.path, "status", "--porcelain", "--untracked-files=all"):
            raise AgentError("只读阶段结束时候选副本有未提交的变化，已停止推进。")

    def at(self, commit):
        if git(self.path, "rev-parse", "HEAD") != commit:
            raise AgentError("候选副本的 HEAD 已变化，无法确认交付范围。")

    def capture(self):
        previous = self.run["candidate"] or self.run["base"]
        self.at(previous)
        if not git(self.path, "status", "--porcelain", "--untracked-files=all"):
            raise AgentError("执行 Agent 未提交可记录的文件变更。")
        git(self.path, "add", "-A", "--", ".")
        git(self.path, "-c", "user.name=AutoWork", "-c", "user.email=autowork@localhost",
            "-c", "commit.gpgsign=false", "commit", "-m", f"AutoWork candidate {self.run['round']}")
        commit = git(self.path, "rev-parse", "HEAD")
        self.frozen(commit)
        return commit

    def changed_files(self, candidate):
        return git(self.path, "diff", "--stat", self.run["base"], candidate)
