from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

from orchestrator.agent_protocol import AgentResult, ProtocolError, parse_result
from orchestrator.agent_runtimes import AgentError, AgentRuntimes, cli_result
from orchestrator.candidate import git
from orchestrator.connections import Connections
from orchestrator.live_runs import LiveRuns, exclusive_run
from orchestrator.store import Store
from orchestrator.workbench import Workbench
from orchestrator.agents import MockCoordinator
from test_workbench import call, running_server


class FixtureRuntimes:
    def __init__(self):
        self.calls = []

    def run(self, agent, task, cwd, session, settings, history):
        phase = task["phase"]
        self.calls.append((phase, session))
        if phase == "coordinate":
            if not history:
                return AgentResult("needs_input", "请补充。", ["应当显示什么？"]), None
            return AgentResult("ready", "范围已确认。", []), None
        if phase in ("implement", "repair"):
            (cwd / "greeting.txt").write_text(f"Revision {task['round']}\n", encoding="utf-8")
        if phase == "review":
            if task["round"] == 1:
                return AgentResult("changes_requested", "初版未达标。", ["修正第一行。"]), "review-session"
            return AgentResult("pass", "第二轮检查通过。", ["首行与验收条件一致。"]), "review-session"
        return AgentResult("done", phase, []), "write-session" if phase in ("implement", "repair") else phase + "-session"


@contextmanager
def fixture():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        git(source, "init")
        (source / "greeting.txt").write_text("Original\n", encoding="utf-8")
        git(source, "add", ".")
        git(source, "-c", "user.email=test@localhost", "-c", "user.name=Test", "commit", "-m", "Baseline")
        store = Store(root / "data")
        workbench = Workbench(store, MockCoordinator())
        project = workbench.create("AutoWork 自用测试", "修改候选副本中的问候。")
        runtimes = FixtureRuntimes()
        with patch("orchestrator.connections.resolve_command", return_value="cli"), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fixture"}):
            yield source, store, project["id"], runtimes


class LiveRunTest(TestCase):
    @staticmethod
    def advance(live, project):
        with live.store.connection() as db:
            run = live.latest(db, project)
        live.advance([run["id"]])
        with live.store.connection() as db:
            return live.latest(db, project)

    def test_waiting_refinement_reuses_sessions_and_freezes_candidate(self):
        with fixture() as (source, store, project, runtimes):
            live = LiveRuns(store, Connections(store), runtimes)
            original = git(source, "rev-parse", "HEAD")
            run = live.start(project, str(source), "Revision 2", local_coordinator=False)
            self.assertEqual(self.advance(live, project)["status"], "waiting_input")
            self.assertTrue(live.add_input(project, "首行应为 Revision 2。"))
            store = Store(store.root)
            live = LiveRuns(store, Connections(store), runtimes)
            for _ in range(7):
                done = self.advance(live, project)
            self.assertEqual((done["status"], done["round"]), ("completed", 2))
            self.assertEqual(git(source, "rev-parse", "HEAD"), original)
            self.assertEqual((source / "greeting.txt").read_text(), "Original\n")
            candidate = store.root / "runs" / run["id"] / "checkout"
            self.assertEqual(git(candidate, "rev-parse", "HEAD"), done["candidate"])
            self.assertEqual(git(candidate, "remote"), "")
            self.assertEqual((candidate / "greeting.txt").read_text(), "Revision 2\n")
            self.assertIn(("repair", "write-session"), runtimes.calls)
            self.assertIn(("review", "review-session"), runtimes.calls)
            document = store.read_document(project, f"runs/{run['id']}/delivery.md")
            self.assertIn(done["candidate"], document["content"])
            self.assertTrue((store.root / "projects" / project / document["path"]).is_file())
            self.assertIn(done["candidate"], store.read_memory(project)["content"])
            with self.assertRaises(KeyError):
                store.read_document(project, "../../workbench.sqlite3")

    def test_abandoned_task_is_not_repeated(self):
        with fixture() as (source, store, project, runtimes):
            live = LiveRuns(store, Connections(store), runtimes)
            view = live.start(project, str(source), "验收", local_coordinator=True)
            folder = store.root / "runs" / view["id"]
            folder.mkdir(parents=True)
            with store.connection() as db:
                live.save(db, live.latest(db, project), "running", "Dispatched")
            with exclusive_run(folder / "worker.lock") as acquired:
                self.assertTrue(acquired)
                self.assertEqual(self.advance(live, project)["status"], "running")
            self.assertEqual(self.advance(live, project)["status"], "interrupted")
            self.assertEqual(runtimes.calls, [])

    def test_http_starts_run_and_replays_delivery_after_refresh(self):
        with fixture() as (source, store, project, runtimes):
            with running_server(store.root, runtimes=runtimes) as url:
                endpoint = f"/api/projects/{project}"
                run = call(url, endpoint + "/run", {
                    "action": "start", "source": str(source), "acceptance": "Revision 2", "local_coordinator": True,
                })
                with self.assertRaises(HTTPError) as error:
                    call(url, endpoint + "/run", {"action": "start", "source": str(source), "acceptance": "验收", "local_coordinator": True})
                self.assertEqual(error.exception.code, 409)
                error.exception.close()
                cursor = 0
                deadline = monotonic() + 12
                while monotonic() < deadline:
                    feed = call(url, endpoint + f"/events?after={cursor}")
                    cursor = feed["cursor"]
                    terminal = [event["data"] for event in feed["events"] if event["kind"] == "live.changed"
                                and event["data"]["status"] in ("completed", "blocked")]
                    if terminal:
                        break
                    sleep(.15)
                self.assertTrue(terminal)
                self.assertEqual(terminal[-1]["status"], "completed")
                content = call(url, endpoint + f"/document?path=runs/{run['id']}/delivery.md")
                self.assertIn(terminal[-1]["candidate"], content["content"])


class ProtocolTest(TestCase):
    def test_deepseek_api_uses_the_same_protocol_and_conversation_history(self):
        task = {"task_id": "task-1", "candidate": None, "phase": "coordinate"}
        reply = {"version": 1, "task_id": "task-1", "candidate": None,
                 "status": "ready", "summary": "Confirmed.", "items": []}
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]}
        settings = {"base_url": "https://example.test/v1", "model": "fixture", "effort": "", "key_env": "AUTOWORK_TEST_KEY"}
        history = [{"role": "assistant", "content": "What should the test check?"}]
        with patch("orchestrator.agent_runtimes.build_opener") as opener, patch.dict("os.environ", {"AUTOWORK_TEST_KEY": "fake"}):
            call_api = opener.return_value.open
            call_api.return_value = BytesIO(json.dumps(response).encode())
            result, session = AgentRuntimes(None).run("deepseek", task, None, None, settings, history)
        request = call_api.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer fake")
        payload = json.loads(request.data)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][1], history[0])
        self.assertEqual(result.status, "ready")
        self.assertIsNone(session)

    def test_result_requires_matching_identifiers_and_phase(self):
        task = {"task_id": "task-1", "candidate": "commit-1", "phase": "review"}
        result = {"version": 1, "task_id": "task-1", "candidate": "commit-1",
                  "status": "pass", "summary": "Summary", "items": ["Checked candidate."]}
        self.assertEqual(parse_result(json.dumps(result), task).status, "pass")
        for change in ({"candidate": "commit-2"}, {"task_id": "other"}, {"status": "done"}, {"items": []}, {"version": True}):
            with self.subTest(change=change), self.assertRaises(ProtocolError):
                parse_result(json.dumps(result | change), task)
        with self.assertRaises(ProtocolError):
            parse_result(json.dumps(result)[:-1] + ', "status": "pass"}', task)

    def test_jsonl_reads_only_terminal_agent_answer_and_exact_session(self):
        dsh = [
            {"type": "session", "sessionId": "session-1"},
            {"type": "text", "text": "partial"},
            {"type": "final", "sessionId": "session-1", "reason": {"kind": "completed"}, "text": "answer"},
        ]
        codex = [
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
            {"type": "turn.completed"},
        ]
        for agent, items, session in (("dsh", dsh, "session-1"), ("codex", codex, "thread-1")):
            for records, expected_session in ((items[:-1], session), (items, "different-session")):
                with self.assertRaises(AgentError):
                    cli_result(agent, "\n".join(map(json.dumps, records)), expected_session)
            self.assertEqual(cli_result(agent, "\n".join(map(json.dumps, items)), session), ("answer", session))
        dsh[-1]["reason"] = {"kind": "error"}
        with self.assertRaises(AgentError):
            cli_result("dsh", "\n".join(map(json.dumps, dsh)), "session-1")
