from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from orchestrator.demo import Demo, FIRST_VERSION, MAX_REPAIRS, STEPS, revision, simulate_import
from orchestrator.store import Store


class DemoTest(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root)
        self.now = 0
        self.demo = self.new_demo()

    def new_demo(self):
        return Demo(self.store, delay=1, clock=lambda: self.now)

    def advance(self, count):
        for _ in range(count):
            self.now += 1
            self.demo.tick()

    def test_runs_both_policies_and_preserves_prior_attempt(self):
        for policy, name in (("keep", "网关"), ("update", "网关新版")):
            with self.subTest(policy=policy):
                project_id = self.demo.open(fresh=True)["id"]
                self.demo.control(project_id, "confirm", policy)
                self.advance(len(STEPS) + 1)
                events = self.store.events(project_id)
                proofs = [(event["seq"], event["data"]) for event in events if event["kind"] == "test.completed"]
                self.assertEqual(len(proofs), 3)
                self.assertEqual(proofs[0][1]["counts"], [2, 4, 5])
                self.assertEqual(proofs[0][1]["candidate"], FIRST_VERSION)
                self.assertFalse(proofs[0][1]["passed"])
                self.assertEqual(proofs[1][1]["counts"], [2, 2, 2])
                self.assertEqual(proofs[1][1]["candidate"], revision(2))
                self.assertNotEqual(proofs[1][1]["name"], name)
                self.assertFalse(proofs[1][1]["passed"])
                self.assertEqual(proofs[2][1]["candidate"], revision(3))
                self.assertEqual(proofs[2][1]["name"], name)
                self.assertTrue(proofs[2][1]["passed"])
                accepted = [event for event in events if event["kind"] == "stage.accepted"]
                self.assertEqual(len(accepted), 2)
                self.assertLess(proofs[2][0], accepted[0]["seq"])
                self.assertEqual(events[-1]["data"]["status"], "completed")
                folder = self.root / "projects" / project_id
                self.assertIn(name, (folder / "evidence" / "交付摘要.md").read_text(encoding="utf-8"))
                memory = (folder / "MEMORY.md").read_text(encoding="utf-8")
                self.assertIn("演示状态 已交付", memory)
                self.assertIn("[rev-03](reviews/rev-03.md)", memory)
                contexts = {event["data"]["channel"]: event["data"]["context"] for event in events
                            if event["kind"] == "message.added" and "context" in event["data"]}
                for agent in ("deepseek", "dsh", "codex"):
                    self.assertEqual(contexts[agent]["project_id"], project_id)
                    self.assertEqual(contexts[agent]["persona_file"], f"orchestrator/personas/{agent}/SOUL.md")
                self.assertEqual(len(list((folder / "reviews").glob("rev-*.md"))), 3)
                self.assertEqual((folder / "progress.md").read_text(encoding="utf-8").count("已完成"), 2)
        self.assertEqual(len(self.store.list_projects()), 2)

    def test_pause_and_restart_preserve_checkpoint(self):
        project_id = self.demo.open()["id"]
        self.demo.control(project_id, "confirm", "keep")
        self.advance(6)
        self.demo.control(project_id, "pause")
        paused = self.store.events(project_id)
        self.assertEqual(paused[-1]["data"]["round"], 3)
        memory = self.store.read_memory(project_id)["content"]
        self.assertIn("演示状态 已暂停", memory)
        self.store = Store(self.root)
        self.demo = self.new_demo()
        self.advance(20)
        self.assertEqual(self.store.events(project_id), paused)
        self.demo.control(project_id, "resume")
        self.advance(1)
        self.store = Store(self.root)
        self.demo = self.new_demo()
        self.advance(len(STEPS))
        events = self.store.events(project_id)
        self.assertEqual(sum(event["data"].get("path") == "plan/总体方案.md" for event in events), 1)
        self.assertEqual(events[-1]["data"]["status"], "completed")

    def test_repair_limit_never_delivers(self):
        project_id = self.demo.open()["id"]
        self.demo.control(project_id, "confirm", "keep")
        def fail(candidate, policy, scenario):
            return simulate_import(candidate, policy, scenario) | {"counts": [2, 4, 5], "passed": False}

        with patch("orchestrator.demo.simulate_import", side_effect=fail):
            self.advance(20)
        events = self.store.events(project_id)
        self.assertEqual(events[-1]["data"]["status"], "blocked")
        self.assertEqual(sum(event["kind"] == "test.completed" for event in events), MAX_REPAIRS + 1)
        self.assertFalse(any(event["kind"] == "stage.accepted" for event in events))
        self.assertFalse((self.root / "projects" / project_id / "evidence" / "交付摘要.md").exists())

    def test_rejects_result_for_a_different_candidate(self):
        project_id = self.demo.open()["id"]
        self.demo.control(project_id, "confirm", "keep")
        self.advance(3)
        proof = simulate_import(revision(3), "keep", 2)
        with patch("orchestrator.demo.simulate_import", return_value=proof):
            self.advance(1)
        events = self.store.events(project_id)
        self.assertEqual(events[-1]["data"]["status"], "blocked")
        self.assertFalse(any(event["kind"] == "stage.accepted" for event in events))

    def test_completes_a_legacy_run_restored_at_recheck(self):
        root = self.root / "legacy"
        root.mkdir()
        project_id = "a" * 32
        db = sqlite3.connect(root / "workbench.sqlite3")
        try:
            with db:
                db.executescript("""
                    CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, stage TEXT, demo INTEGER, created_at TEXT);
                    CREATE TABLE demo_runs (project_id TEXT PRIMARY KEY, step INTEGER, status TEXT, policy TEXT, due_at REAL);
                """)
                db.execute("INSERT INTO projects VALUES (?, '旧演示', 'intake', 1, '')", (project_id,))
                db.execute("INSERT INTO demo_runs VALUES (?, 6, 'running', 'keep', 0)", (project_id,))
        finally:
            db.close()
        self.store = Store(root)
        self.demo = self.new_demo()
        self.advance(2)
        events = self.store.events(project_id)
        self.assertTrue(next(event["data"]["passed"] for event in events if event["kind"] == "test.completed"))
        self.assertEqual(events[-1]["data"]["round"], 2)
        self.assertEqual(events[-1]["data"]["status"], "completed")
