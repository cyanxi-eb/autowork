from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from orchestrator.demo import Demo, FIRST_VERSION, FIXED_VERSION, STEPS
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
                self.advance(len(STEPS))
                events = self.store.events(project_id)
                proofs = [(event["seq"], event["data"]) for event in events if event["kind"] == "test.completed"]
                self.assertEqual(len(proofs), 2)
                self.assertEqual(proofs[0][1]["counts"], [2, 4, 5])
                self.assertEqual(proofs[0][1]["candidate"], FIRST_VERSION)
                self.assertFalse(proofs[0][1]["passed"])
                self.assertEqual(proofs[1][1]["counts"], [2, 2, 2])
                self.assertEqual(proofs[1][1]["candidate"], FIXED_VERSION)
                self.assertEqual(proofs[1][1]["name"], name)
                self.assertTrue(proofs[1][1]["passed"])
                accepted = [event for event in events if event["kind"] == "stage.accepted"]
                self.assertEqual(len(accepted), 2)
                self.assertLess(proofs[1][0], accepted[0]["seq"])
                self.assertEqual(events[-1]["data"]["status"], "completed")
                folder = self.root / "projects" / project_id
                self.assertIn(name, (folder / "evidence" / "交付摘要.md").read_text(encoding="utf-8"))
                self.assertEqual((folder / "progress.md").read_text(encoding="utf-8").count("已完成"), 2)
        self.assertEqual(len(self.store.list_projects()), 2)

    def test_pause_and_restart_preserve_checkpoint(self):
        project_id = self.demo.open()["id"]
        self.demo.control(project_id, "confirm", "keep")
        self.advance(2)
        self.demo.control(project_id, "pause")
        paused = self.store.events(project_id)
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

    def test_failed_repair_never_delivers(self):
        project_id = self.demo.open()["id"]
        self.demo.control(project_id, "confirm", "keep")
        self.advance(5)
        failed = {"candidate": FIXED_VERSION, "policy": "keep", "counts": [2, 4, 5], "name": "网关", "passed": False}
        with patch("orchestrator.demo.simulate_import", return_value=failed):
            self.advance(1)
        self.advance(20)
        events = self.store.events(project_id)
        self.assertEqual(events[-1]["data"]["status"], "blocked")
        self.assertFalse(any(event["kind"] == "stage.accepted" for event in events))
        self.assertFalse((self.root / "projects" / project_id / "evidence" / "交付摘要.md").exists())
