import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .agent_context import persona_path
from .agent_runtimes import AgentRuntimes
from .agents import MockCoordinator
from .connections import Connections
from .demo import Demo, DemoConflict, POLICIES, STEPS
from .live_runs import LiveRuns, PHASES, RunConflict
from .store import Store
from .workbench import Workbench


STATIC = Path(__file__).resolve().parent / "web"
SOURCE = STATIC.parent.parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
PROJECT = re.compile(r"/api/projects/([a-f0-9]{32})(?:/(events|messages|demo|memory|run|document))?")
AGENT = re.compile(r"/api/agents/(deepseek|dsh|codex)/(connection|persona)")


def required_text(body, key, limit):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{key} 不能为空，且长度须在 {limit} 字符以内。")
    value.encode("utf-8")
    return value.strip()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def respond(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def json_response(self, status, content):
        self.respond(status, json.dumps(content, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def local_request(self):
        host = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if self.headers.get("Host") != host or (origin is not None and origin != f"http://{host}"):
            self.json_response(403, {"error": "请从本机工作台地址访问。"})
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        address = urlsplit(self.path)
        path = address.path
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            self.respond(200, (STATIC / filename).read_bytes(), content_type)
            return
        workbench = self.server.workbench
        if path == "/api/workbench":
            snapshot = {
                "mode": "live", "agents": workbench.agents(), "projects": workbench.store.list_projects(),
                "demo": {"steps": STEPS, "policies": POLICIES},
                "live": {"phases": {key: value[1] for key, value in PHASES.items()},
                         "default_source": str(SOURCE) if (SOURCE / ".git").exists() else ""},
            }
            if self.server.desktop_instance:
                snapshot["desktop_instance"] = self.server.desktop_instance
            self.json_response(200, snapshot)
            return
        match = PROJECT.fullmatch(path)
        if match and match[2] in (None, "events", "memory", "document"):
            self.get_project(match[1], match[2], address.query)
            return
        match = AGENT.fullmatch(path)
        if match:
            if match[2] == "connection":
                self.json_response(200, self.server.connections.get(match[1]))
            else:
                try:
                    file = persona_path(match[1])
                    self.json_response(200, {"path": str(file), "content": file.read_text(encoding="utf-8-sig")})
                except (OSError, UnicodeError):
                    self.json_response(409, {"error": "人设文件不可读，请检查本地 Markdown 文件。"})
            return
        self.json_response(404, {"error": "找不到此内容。"})

    def get_project(self, project_id, resource, query):
        store = self.server.workbench.store
        try:
            if resource == "memory":
                self.json_response(200, store.read_memory(project_id))
                return
            if resource == "document":
                self.json_response(200, store.read_document(project_id, parse_qs(query).get("path", [""])[0]))
                return
            project = store.get_project(project_id)
            if resource is None:
                self.json_response(200, project)
                return
            after = int(parse_qs(query).get("after", ["0"])[0])
            if not 0 <= after < 2 ** 63:
                raise ValueError
            events = store.events(project_id, after)
            cursor = events[-1]["seq"] if events else after
            self.json_response(200, {"events": events, "cursor": cursor})
        except KeyError:
            self.json_response(404, {"error": "项目或文档不存在。"})
        except (OSError, UnicodeError):
            self.json_response(409, {"error": "项目文档不可读，请检查本地 Markdown 文件。"})
        except ValueError:
            self.json_response(400, {"error": "事件游标无效。"})

    def read_json(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("需要 JSON 请求。")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 65536:
            raise ValueError("请求体大小无效。")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求必须为 JSON 对象。")
        return body

    def do_POST(self):
        if not self.local_request():
            return
        workbench = self.server.workbench
        path = urlsplit(self.path).path
        match = PROJECT.fullmatch(path)
        connection = AGENT.fullmatch(path)
        try:
            body = self.read_json()
            if connection and connection[2] == "connection":
                self.json_response(200, self.server.connections.save(connection[1], body))
            elif path == "/api/projects":
                project = workbench.create(required_text(body, "name", 80), required_text(body, "request", 8000))
                self.json_response(201, project)
            elif path == "/api/demo":
                self.json_response(200, self.server.demo.open(fresh=body.get("fresh") is True))
            elif match and match[2] == "demo":
                self.server.demo.control(match[1], required_text(body, "action", 16), body.get("policy"))
                self.json_response(200, {"accepted": True})
            elif match and match[2] == "run":
                if body.get("action") == "cancel_waiting":
                    self.json_response(200, self.server.live.cancel_waiting(match[1]))
                elif body.get("action") == "retry_review":
                    self.json_response(202, self.server.live.retry_review(match[1]))
                else:
                    local = body.get("local_coordinator", False)
                    if type(local) is not bool or body.get("action") != "start":
                        raise ValueError("运行操作或协调设置无效。")
                    run = self.server.live.start(
                        match[1], required_text(body, "source", 1024),
                        required_text(body, "acceptance", 8000), local_coordinator=local,
                    )
                    self.json_response(202, run)
            elif match and match[2] == "messages":
                if workbench.store.get_project(match[1])["demo"]:
                    raise DemoConflict("请用演示操作区选择策略或控制运行。")
                user_text = required_text(body, "text", 8000)
                if not self.server.live.add_input(match[1], user_text):
                    workbench.add_input(match[1], user_text)
                self.json_response(201, {"accepted": True})
            else:
                self.json_response(404, {"error": "找不到此操作。"})
        except KeyError:
            self.json_response(404, {"error": "项目不存在。"})
        except (json.JSONDecodeError, UnicodeError, TimeoutError):
            self.json_response(400, {"error": "请求内容无效。"})
        except OSError:
            self.json_response(409, {"error": "人设或项目记忆不可读，请检查本地 Markdown 文件。"})
        except (DemoConflict, RunConflict) as error:
            self.json_response(409, {"error": str(error)})
        except ValueError as error:
            self.json_response(400, {"error": str(error)})


class WorkbenchServer(ThreadingHTTPServer):
    def service_actions(self):
        try:
            self.demo.tick()
            self.live.tick()
        except sqlite3.Error:
            logging.exception("Could not commit the next demo step")


def create_server(data_dir, port, *, demo_delay=1.8, runtimes=None):
    store = Store(data_dir)
    server = WorkbenchServer(("127.0.0.1", port), Handler)
    server.workbench = Workbench(store, MockCoordinator())
    server.demo = Demo(store, delay=demo_delay)
    server.connections = Connections(store)
    server.live = LiveRuns(store, server.connections, runtimes or AgentRuntimes(server.connections))
    server.desktop_instance = None
    return server


def main():
    parser = argparse.ArgumentParser(description="启动本机项目工作台")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / ".orchestrator")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--desktop", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    with create_server(args.data_dir, args.port) as server:
        if args.desktop:
            server.desktop_instance = uuid4().hex
            endpoint = {
                "port": server.server_port, "instance": server.desktop_instance,
                "root": str(Path(__file__).resolve().parent.parent),
            }
            Store.write_file(server.workbench.store.root / "desktop-endpoint.json", json.dumps(endpoint))
        print(f"项目工作台  http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
