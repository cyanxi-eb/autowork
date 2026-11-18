import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .agents import MockCoordinator
from .store import Store
from .workbench import Workbench


STATIC = Path(__file__).resolve().parent / "web"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
PROJECT = re.compile(r"/api/projects/([a-f0-9]{32})(?:/(events|messages))?")


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
            self.json_response(200, {"mode": "mock", "agents": workbench.agents(), "projects": workbench.store.list_projects()})
            return
        match = PROJECT.fullmatch(path)
        if match and match[2] in (None, "events"):
            self.get_project(match[1], match[2], address.query)
            return
        self.json_response(404, {"error": "找不到此内容。"})

    def get_project(self, project_id, resource, query):
        store = self.server.workbench.store
        try:
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
            self.json_response(404, {"error": "项目不存在。"})
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
        try:
            body = self.read_json()
            if path == "/api/projects":
                project = workbench.create(required_text(body, "name", 80), required_text(body, "request", 8000))
                self.json_response(201, project)
            elif path == "/api/demo":
                self.json_response(200, workbench.demo())
            elif match and match[2] == "messages":
                workbench.add_input(match[1], required_text(body, "text", 8000))
                self.json_response(201, {"accepted": True})
            else:
                self.json_response(404, {"error": "找不到此操作。"})
        except KeyError:
            self.json_response(404, {"error": "项目不存在。"})
        except (json.JSONDecodeError, UnicodeError, TimeoutError):
            self.json_response(400, {"error": "请求内容无效。"})
        except ValueError as error:
            self.json_response(400, {"error": str(error)})


def create_server(data_dir, port):
    workbench = Workbench(Store(data_dir), MockCoordinator())
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.workbench = workbench
    return server


def main():
    parser = argparse.ArgumentParser(description="启动本机项目工作台")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / ".orchestrator")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with create_server(args.data_dir, args.port) as server:
        print(f"项目工作台  http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
