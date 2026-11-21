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
def running_server(root, **options):
    with create_server(root, 0, **options) as server:
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
    def test_personas_and_project_memories_stay_separate_on_restart(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with running_server(root) as url:
                roles = {agent: call(url, f"/api/agents/{agent}/persona") for agent in ("deepseek", "dsh", "codex")}
                self.assertEqual(len({item["content"] for item in roles.values()}), 3)
                first = call(url, "/api/projects", {"name": "项目甲", "request": "项目甲的独立需求"})
                second = call(url, "/api/projects", {"name": "项目乙", "request": "项目乙的独立需求"})
                first_api, second_api = (f"/api/projects/{item['id']}" for item in (first, second))
                first_memory = call(url, first_api + "/memory")
                second_memory = call(url, second_api + "/memory")
                self.assertIn("项目甲的独立需求", first_memory["content"])
                self.assertNotIn("项目甲的独立需求", second_memory["content"])
                path = Path(first_memory["path"])
                original = first_memory["content"]
                path.write_text(original.replace("## 人工备注", "## 人工备注\n\n甲项目的人工补充"), encoding="utf-8")
                call(url, first_api + "/messages", {"text": "继续补充验收条件"})
                events = call(url, first_api + "/events")["events"]
                self.assertEqual(events[-1]["data"]["context"]["project_id"], first["id"])
                self.assertNotIn("甲项目的人工补充", call(url, second_api + "/memory")["content"])
            Path(second_memory["path"]).unlink()
            with running_server(root) as url:
                self.assertIn("甲项目的人工补充", call(url, first_api + "/memory")["content"])
                restored = call(url, second_api + "/memory")
                self.assertEqual(restored["content"], second_memory["content"])

    def test_connection_settings_are_per_agent_persisted_and_exclude_secrets(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with running_server(root) as url:
                endpoint = "/api/agents/deepseek/connection"
                current = call(url, endpoint)
                self.assertEqual(current["kind"], "api")
                settings = current["settings"] | {"model": "custom-model", "effort": "high"}
                self.assertEqual(call(url, endpoint, settings)["settings"], settings)
                self.assertEqual(call(url, "/api/agents/dsh/connection")["kind"], "cli")
                for invalid in (settings | {"api_key": "fake-secret"}, settings | {"base_url": "https://example.test/?token=fake"}):
                    with self.assertRaises(HTTPError) as error:
                        call(url, endpoint, invalid)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
            with running_server(root) as url:
                self.assertEqual(call(url, endpoint)["settings"], settings)

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
            demo_endpoint = f"/api/projects/{demo['id']}"
            self.assertTrue(call(url, demo_endpoint + "/demo", {"action": "confirm", "policy": "update"})["accepted"])
            with self.assertRaises(HTTPError) as error:
                call(url, demo_endpoint + "/demo", {"action": "confirm", "policy": "keep"})
            self.assertEqual(error.exception.code, 409)
            error.exception.close()
            project = call(url, "/api/projects", {"name": "隔离测试", "request": "新需求"})
            self.assertFalse(project["demo"])
            feeds = [call(url, f"/api/projects/{item['id']}/events")["events"] for item in (demo, project)]
            demo_status = [event["data"]["status"] for event in feeds[0] if event["kind"] == "demo.changed"]
            self.assertEqual(demo_status[:2], ["waiting_input", "running"])
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
