import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .agent_protocol import output_schema, parse_result, prompt


MAX_STREAM = 16 * 1024 * 1024
RUN_TIMEOUT = 900
SESSION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}")


class AgentError(RuntimeError):
    pass


def resolve_command(agent, command):
    resolved = shutil.which(command)
    if resolved:
        return resolved
    candidate = Path(command).expanduser()
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate)
    if os.name != "nt" or command != agent:
        return None
    local = os.environ.get("LOCALAPPDATA")
    if agent == "codex" and local:
        versions = (Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")
        return str(max(versions, key=lambda path: path.stat().st_mtime, default="")) or None
    if agent != "dsh":
        return None
    drives = os.listdrives() if hasattr(os, "listdrives") else [f"{letter}:\\" for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ"]
    roots = [Path(local) / "Programs"] if local else []
    if os.environ.get("ProgramFiles"):
        roots.append(Path(os.environ["ProgramFiles"]))
    roots.extend(Path(drive) for drive in drives)
    for root in roots:
        shim = root / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
        if shim.is_file():
            return str(shim)
    return None


def cli_command(agent, path):
    executable = Path(path)
    if os.name == "nt" and agent == "dsh" and executable.name.lower() == "dsh.cmd":
        if executable.parent.name == "bin" and executable.parent.parent.name == "cli":
            resources = executable.parents[3]
            desktop = resources.parent / "DeepSeek Harness.exe"
            archive = resources / "app.asar"
            if desktop.is_file() and archive.is_file():
                entry = archive / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-desktop-host" / "lib" / "cli.js"
                return [str(desktop), "--expose-internals", str(entry)], {"ELECTRON_RUN_AS_NODE": "1"}
    return [path], {}


def invoke(command, prompt_text, cwd, environment, timeout=RUN_TIMEOUT):
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        try:
            process = subprocess.run(
                command, cwd=cwd, input=prompt_text.encode("utf-8"), stdout=stdout, stderr=stderr,
                env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0"} | environment,
                timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as error:
            raise AgentError("CLI 超时，执行结果待核实。") from error
        except OSError as error:
            raise AgentError("无法启动本地 CLI，请检查可执行文件与工作目录。") from error
        if process.returncode:
            stderr.seek(0)
            diagnostic = stderr.read(12_000).lower()
            stdout.seek(0)
            diagnostic += stdout.read(12_000).lower()
            if any(word in diagnostic for word in (b"unauthorized", b"not logged", b"no credential", b"api key",
                                                    b"authentication", b"missing_credential", b"account_sign_in_required")):
                raise AgentError("CLI 鉴权失败，请确认已登录或配置了当前模型的凭据。")
            if b"home directory" in diagnostic:
                raise AgentError("CLI 无法读取本机用户配置目录。")
            if b"session" in diagnostic and any(word in diagnostic for word in (b"does not exist", b"not found")):
                raise AgentError("原 CLI 会话无法续接，已保留本轮记录。")
            raise AgentError(f"CLI 退出码 {process.returncode}，执行结果待核实。")
        stdout.seek(0)
        data = stdout.read(MAX_STREAM + 1)
        if len(data) > MAX_STREAM:
            raise AgentError("CLI 输出超过读取上限。")
        return data.decode("utf-8-sig", errors="replace")


def cli_result(agent, raw, expected_session):
    session, answer, complete = None, None, False
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if agent == "dsh":
            if kind == "session":
                session = event.get("sessionId")
            elif kind == "final" and session and event.get("sessionId") == session:
                reason = event.get("reason")
                if isinstance(reason, dict):
                    reason = reason.get("kind")
                answer, complete = event.get("text"), reason == "completed"
        elif kind == "thread.started":
            session = event.get("thread_id")
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                answer = item.get("text")
        elif kind == "turn.completed":
            complete = True
        elif kind == "turn.failed":
            complete = False
    if not complete or not isinstance(answer, str) or not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        raise AgentError("CLI 未返回完整的会话和终态结果。")
    if expected_session and session != expected_session:
        raise AgentError("CLI 返回了其他会话，无法确认本次续接。")
    return answer, session


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dsh_options(settings, session, write, folder):
    options = ["--profile", "headless"]
    provider = settings.get("provider", "")
    key_available = bool(os.environ.get("DEEPSEEK_API_KEY"))
    if provider or not key_available or settings["model"] or settings["effort"]:
        # Restore DSH's native credential store for this invocation only.
        rows = [{"id": name, "disabled": False} for name in ("credentials", "authorization", "deepseek-account")]
        model = {
            "provider": provider or ("deepseek-official" if key_available else "deepseek-account"),
            "model": settings["model"] or "deepseek-flash",
        }
        if settings["effort"]:
            model["reasoningEffort"] = settings["effort"]
        rows.append({"id": "agent-default-model", "config": model})
        overlay = folder / "headless.yml"
        overlay.write_text(json.dumps(rows), encoding="utf-8")
        options += ["--patch", str(overlay)]
    options += ["--json", "--permission-mode", "workspace-write" if write else "read-only", "--approval-policy", "never"]
    if session:
        options += ["--session-id", session]
    return options


class AgentRuntimes:
    def __init__(self, connections):
        self.connections = connections

    def run(self, agent, task, cwd, session, settings, history):
        if agent == "deepseek":
            key = os.environ.get(settings["key_env"])
            if not key:
                raise AgentError("未检测到 DeepSeek API 密钥。")
            answer = self.deepseek(task, settings, key, history)
            return parse_result(answer, task), None

        executable = resolve_command(agent, settings["command"])
        if not executable:
            raise AgentError(f"未找到 {agent} CLI，请在接口面板设置命令路径。")
        command, environment = cli_command(agent, executable)
        write = task["phase"] in ("implement", "repair")
        with TemporaryDirectory(prefix="agent-", dir=self.connections.store.root) as temporary:
            if agent == "dsh":
                options = dsh_options(settings, session, write, Path(temporary))
            else:
                schema = Path(temporary) / "result.json"
                schema.write_text(json.dumps(output_schema(task["phase"])), encoding="utf-8")
                options = ["--ask-for-approval", "never", "--sandbox", "read-only", "exec", "--json",
                           "--color", "never", "--output-schema", str(schema)]
                if settings["model"]:
                    options += ["--model", settings["model"]]
                if settings["effort"]:
                    options += ["-c", "model_reasoning_effort=" + json.dumps(settings["effort"])]
                if session:
                    options += ["resume", session]
            raw = invoke(command + options + ["-"], prompt(task), cwd, environment)
            answer, native_session = cli_result(agent, raw, session)
        return parse_result(answer, task), native_session

    @staticmethod
    def deepseek(task, settings, key, history):
        payload = {
            "model": settings["model"] or "deepseek-flash",
            "messages": [{"role": "system", "content": "你是 AutoWork 的需求协调 Agent。每轮按任务的 JSON 协议回复。"}]
                        + history + [{"role": "user", "content": prompt(task)}],
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        }
        if settings["effort"]:
            payload["reasoning_effort"] = settings["effort"]
        request = Request(
            settings["base_url"].rstrip("/") + "/chat/completions",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=90) as response:
                raw = response.read(512_001)
            if len(raw) > 512_000:
                raise AgentError("DeepSeek API 返回超过读取上限。")
            choice = json.loads(raw)["choices"][0]
            if choice["finish_reason"] != "stop":
                raise AgentError("DeepSeek API 没有返回完整结果。")
            return choice["message"]["content"]
        except HTTPError as error:
            error.close()
            raise AgentError(f"DeepSeek API 返回 HTTP {error.code}。") from error
        except (URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as error:
            raise AgentError("无法取得 DeepSeek API 的结构化回复，请检查地址、模型和凭据。") from error
