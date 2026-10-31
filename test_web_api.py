"""
TaskTact - Web API Integration Test Suite
=========================================
Tests the HTTP REST endpoints, serialization, error handling,
and server telemetry using the Python standard library.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import tasktact
import web_app


class TestWebAPI(unittest.TestCase):
    """Integration test suite for TaskTact HTTP REST API."""

    @classmethod
    def setUpClass(cls):
        # Create an isolated temporary test directory
        cls.test_dir = tempfile.mkdtemp()
        cls.orig_tasks_file = tasktact.TASKS_FILE
        cls.orig_backup_file = tasktact.BACKUP_FILE
        cls.orig_config_file = tasktact.CONFIG_FILE
        cls.orig_activity_file = web_app.ACTIVITY_FILE

        tasktact.TASKS_FILE = os.path.join(cls.test_dir, "test_tasks.json")
        tasktact.BACKUP_FILE = os.path.join(cls.test_dir, "test_tasks.json.bak")
        tasktact.CONFIG_FILE = os.path.join(cls.test_dir, "test_config.json")
        web_app.ACTIVITY_FILE = os.path.join(cls.test_dir, "test_activity.json")

        # Seed initial test tasks
        cls.sample_tasks = [
            {
                "id": 1,
                "title": "API Test Task One",
                "category": "Testing",
                "priority": "High",
                "due": "2026-10-30",
                "status": "Pending",
                "created": "2026-10-29 10:00:00",
                "completed": "",
                "notes": "Verify REST endpoints",
                "tags": ["api", "test"],
                "recurrence": "none"
            }
        ]
        tasktact.safe_save_tasks(cls.sample_tasks)

        # Launch HTTP server on a dedicated test port
        cls.port = 5892
        cls.server = web_app.ReusableThreadingHTTPServer(("127.0.0.1", cls.port), web_app.TaskTactRequestHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

        # Restore original paths
        tasktact.TASKS_FILE = cls.orig_tasks_file
        tasktact.BACKUP_FILE = cls.orig_backup_file
        tasktact.CONFIG_FILE = cls.orig_config_file
        web_app.ACTIVITY_FILE = cls.orig_activity_file

        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def _request(self, method: str, path: str, payload: dict = None) -> tuple:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8")
                if "application/json" in content_type:
                    body = json.loads(raw)
                else:
                    body = raw
                return resp.status, body
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                err_body = json.loads(raw)
            except Exception:
                err_body = raw
            return e.code, err_body

    def test_health_telemetry_endpoint(self):
        """Test GET /api/health returns healthy telemetry."""
        status, body = self._request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        health = body.get("health", {})
        self.assertEqual(health.get("status"), "healthy")
        self.assertIn("tasks_total", health)
        self.assertIn("python_version", health)

    def test_get_tasks_list(self):
        """Test GET /api/tasks lists existing tasks."""
        status, body = self._request("GET", "/api/tasks")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        tasks = body.get("tasks", [])
        self.assertGreaterEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "API Test Task One")

    def test_create_and_delete_task(self):
        """Test creating a new task and then deleting it via REST API."""
        new_task_payload = {
            "title": "Fresh Automated Task",
            "category": "Coding",
            "priority": "Medium",
            "due": "today",
            "notes": "Created by integration test"
        }
        status, body = self._request("POST", "/api/tasks", new_task_payload)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        created_id = body.get("task", {}).get("id")
        self.assertIsNotNone(created_id)

        # Delete the task
        del_status, del_body = self._request("DELETE", f"/api/tasks/{created_id}")
        self.assertEqual(del_status, 200)
        self.assertTrue(del_body.get("success"))

    def test_mark_task_done(self):
        """Test marking a task done via POST /api/tasks/<id>/done and toggling back."""
        status, body = self._request("POST", "/api/tasks/1/done")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))

        # Verify task is now Done
        _, get_body = self._request("GET", "/api/tasks")
        t1 = [t for t in get_body["tasks"] if t["id"] == 1][0]
        self.assertEqual(t1["status"], "Done")

        # Toggle done again to verify reopening task
        status2, body2 = self._request("POST", "/api/tasks/1/done")
        self.assertEqual(status2, 200)
        self.assertTrue(body2.get("success"))
        _, get_body2 = self._request("GET", "/api/tasks")
        t1_reopened = [t for t in get_body2["tasks"] if t["id"] == 1][0]
        self.assertEqual(t1_reopened["status"], "Pending")

    def test_update_task_time_and_alert(self):
        """Test updating due_time and alert_offset via PUT /api/tasks/<id>."""
        update_payload = {
            "title": "API Test Task One Updated",
            "due_time": "16:45",
            "alert_offset": "30m"
        }
        status, body = self._request("PUT", "/api/tasks/1", payload=update_payload)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        self.assertEqual(body["task"]["due_time"], "16:45")
        self.assertEqual(body["task"]["alert_offset"], "30m")

    def test_recurring_preserves_time_and_alert(self):
        """Test that recurring task inherits parent due_time and alert_offset."""
        create_payload = {
            "title": "Daily Standup",
            "due": "today",
            "due_time": "09:30",
            "alert_offset": "15m",
            "recurrence": "daily"
        }
        c_status, c_body = self._request("POST", "/api/tasks", payload=create_payload)
        self.assertEqual(c_status, 200)
        task_id = c_body["task"]["id"]

        # Mark done to trigger recurrence
        d_status, d_body = self._request("POST", f"/api/tasks/{task_id}/done")
        self.assertEqual(d_status, 200)
        new_rec = d_body.get("recurring_task")
        self.assertIsNotNone(new_rec)
        self.assertEqual(new_rec["due_time"], "09:30")
        self.assertEqual(new_rec["alert_offset"], "15m")

    def test_stats_and_activity_endpoints(self):
        """Test GET /api/stats and GET /api/activity endpoints."""
        s_status, s_body = self._request("GET", "/api/stats")
        self.assertEqual(s_status, 200)
        self.assertIn("stats", s_body)

        a_status, a_body = self._request("GET", "/api/activity")
        self.assertEqual(a_status, 200)
        self.assertIn("activities", a_body)

    def test_export_csv_and_txt_endpoints(self):
        """Test GET /api/export/csv and GET /api/export/txt endpoints."""
        csv_status, csv_body = self._request("GET", "/api/export/csv")
        self.assertEqual(csv_status, 200)

        txt_status, txt_body = self._request("GET", "/api/export/txt")
        self.assertEqual(txt_status, 200)


if __name__ == "__main__":
    unittest.main()

