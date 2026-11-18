from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from orchestrator.__main__ import create_server


@contextmanager
def running_server(root):
    with create_server(root, 0) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


def call(url, path, body=None, headers=None):
    raw = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url + path, data=raw, headers={"Content-Type": "application/json"} | (headers or {}))
    with urlopen(request, timeout=4) as response:
        return json.load(response)


class WorkbenchTest(TestCase):
    def test_creation_input_and_restart_repair(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with running_server(root) as url:
                project = call(url, "/api/projects", {"name": "需求测试", "request": "初始需求"})
                endpoint = f"/api/projects/{project['id']}"
                initial = call(url, endpoint + "/events")
                self.assertEqual(initial["events"][0]["kind"], "project.created")
                call(url, endpoint + "/messages", {"text": "补充验收条件"})
                added = call(url, endpoint + f"/events?after={initial['cursor']}")
                self.assertEqual([event["data"]["author"] for event in added["events"]], ["user", "deepseek"])
                self.assertEqual(added["events"][0]["data"]["text"], "补充验收条件")
                markdown = root / "projects" / project["id"] / "requirements" / "需求档案.md"
                self.assertIn("补充验收条件", markdown.read_text(encoding="utf-8"))
            markdown.unlink()
            with running_server(root) as url:
                self.assertEqual(call(url, endpoint), project)
                self.assertIn("补充验收条件", markdown.read_text(encoding="utf-8"))
                self.assertEqual(call(url, endpoint + f"/events?after={added['cursor']}")["events"], [])

    def test_demo_and_new_project_have_separate_sessions(self):
        with TemporaryDirectory() as temp, running_server(Path(temp)) as url:
            demo = call(url, "/api/demo", {})
            self.assertEqual(call(url, "/api/demo", {}), demo)
            project = call(url, "/api/projects", {"name": "隔离测试", "request": "新需求"})
            self.assertFalse(project["demo"])
            feeds = [call(url, f"/api/projects/{item['id']}/events")["events"] for item in (demo, project)]
            demo_sessions = {event["data"].get("session_id") for event in feeds[0]}
            self.assertTrue({"dsh-1", "dsh-2", "codex-1"} <= demo_sessions)
            self.assertEqual({event["data"].get("session_id") for event in feeds[1]}, {None, "main"})

    def test_rejects_invalid_input_and_external_origin(self):
        with TemporaryDirectory() as temp, running_server(Path(temp)) as url:
            for body, headers, expected in (
                ({"name": " ", "request": "需求"}, {}, 400),
                ({"name": "越界请求", "request": "需求"}, {"Origin": "https://example.test"}, 403),
            ):
                with self.assertRaises(HTTPError) as error:
                    call(url, "/api/projects", body, headers)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
            self.assertEqual(call(url, "/api/workbench")["projects"], [])
