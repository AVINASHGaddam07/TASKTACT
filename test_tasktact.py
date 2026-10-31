"""
Unit Test Suite for TaskTact – Intelligent To-Do Manager
=========================================================
Tests core functionality and upgrades:
- Natural date input parsing
- Intelligent scoring & recommendations
- Recurring tasks logic
- Overdue & due-date filters
- Safe atomic persistence & backup recovery
- In-memory undo history
- Search & filtering
- Productivity streak calculation
- CSV & report exports
- Duplicate detection
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta

import tasktact


class TestTaskTact(unittest.TestCase):
    """Test cases for TaskTact logic and utilities."""

    def setUp(self):
        """Create a temporary directory for file-based tests."""
        self.test_dir = tempfile.mkdtemp()
        self.tasks_file = os.path.join(self.test_dir, "test_tasks.json")
        self.backup_file = os.path.join(self.test_dir, "test_tasks.json.bak")
        self.report_file = os.path.join(self.test_dir, "test_report.txt")
        self.csv_file = os.path.join(self.test_dir, "test_tasks.csv")
        # Fixed base date for deterministic tests: Wednesday, Oct 14, 2026
        self.base_date = date(2026, 10, 14)
        tasktact.undo_stack.clear()

    def tearDown(self):
        """Clean up temporary test directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Natural Date Input Parsing Tests (Upgrade B)
    # -------------------------------------------------------------------------
    def test_parse_date_input_natural(self):
        """Test natural date inputs: today, tomorrow, relative offsets, and weekdays."""
        # Empty string
        self.assertEqual(tasktact.parse_date_input("", self.base_date), "")
        
        # 'today'
        self.assertEqual(tasktact.parse_date_input("today", self.base_date), "2026-10-14")
        
        # 'tomorrow' and 'tmrw'
        self.assertEqual(tasktact.parse_date_input("tomorrow", self.base_date), "2026-10-15")
        self.assertEqual(tasktact.parse_date_input("tmrw", self.base_date), "2026-10-15")
        
        # Relative offsets: +3d, +2w, +1m
        self.assertEqual(tasktact.parse_date_input("+3d", self.base_date), "2026-10-17")
        self.assertEqual(tasktact.parse_date_input("+2w", self.base_date), "2026-10-28")
        self.assertEqual(tasktact.parse_date_input("+1m", self.base_date), "2026-11-14")
        
        # Weekday: Base is Wednesday (2). Friday is (4) -> +2 days = Oct 16
        self.assertEqual(tasktact.parse_date_input("fri", self.base_date), "2026-10-16")
        self.assertEqual(tasktact.parse_date_input("friday", self.base_date), "2026-10-16")
        
        # Weekday matching today's weekday advances to next week (+7 days)
        self.assertEqual(tasktact.parse_date_input("wed", self.base_date), "2026-10-21")
        
        # Exact standard format YYYY-MM-DD
        self.assertEqual(tasktact.parse_date_input("2026-12-25", self.base_date), "2026-12-25")

    def test_parse_date_input_invalid(self):
        """Test that invalid date strings raise ValueError."""
        with self.assertRaises(ValueError):
            tasktact.parse_date_input("not-a-date", self.base_date)
        with self.assertRaises(ValueError):
            tasktact.parse_date_input("2026-02-30", self.base_date)

    # -------------------------------------------------------------------------
    # 2. Intelligent Scoring & Recommendations (Upgrade A)
    # -------------------------------------------------------------------------
    def test_scoring_algorithm(self):
        """Test that overdue and higher priority tasks receive higher scores."""
        high_overdue = {
            "id": 1,
            "title": "Fix critical bug",
            "priority": tasktact.PRIORITY_HIGH,
            "due": "2026-10-12",  # 2 days overdue from base Oct 14
            "status": tasktact.STATUS_PENDING,
            "created": "2026-10-10 10:00:00",
        }
        low_future = {
            "id": 2,
            "title": "Clean desk",
            "priority": tasktact.PRIORITY_LOW,
            "due": "2026-10-25",  # 11 days in future
            "status": tasktact.STATUS_PENDING,
            "created": "2026-10-14 09:00:00",
        }
        
        score_high, reason_high = tasktact.score_task(high_overdue, self.base_date)
        score_low, reason_low = tasktact.score_task(low_future, self.base_date)
        
        # High priority (30) + overdue (40 + 4) + age bonus (4) = 78
        self.assertGreater(score_high, score_low)
        self.assertIn("Overdue by 2 days", reason_high)
        self.assertIn("Low priority", reason_low)

    def test_smart_suggestions_ranking(self):
        """Test that get_smart_suggestions returns the top tasks in proper rank."""
        tasks = [
            {"id": 1, "title": "Future Task", "priority": "Low", "due": "2026-10-28", "status": "Pending", "created": "2026-10-14 00:00:00"},
            {"id": 2, "title": "Urgent Due Today", "priority": "High", "due": "2026-10-14", "status": "Pending", "created": "2026-10-14 00:00:00"},
            {"id": 3, "title": "Done Task", "priority": "High", "due": "2026-10-10", "status": "Done", "created": "2026-10-09 00:00:00"},
        ]
        suggestions = tasktact.get_smart_suggestions(tasks, top_n=2, reference_date=self.base_date)
        # Done tasks must be excluded
        self.assertEqual(len(suggestions), 2)
        top_task, top_score, top_reason = suggestions[0]
        self.assertEqual(top_task["id"], 2)
        self.assertIn("Due today", top_reason)

    # -------------------------------------------------------------------------
    # 3. Recurrence Logic (Upgrade C)
    # -------------------------------------------------------------------------
    def test_recurrence_calculation(self):
        """Test calculation of next due dates for daily, weekly, and monthly recurrence."""
        curr_due = "2026-10-14"
        next_daily = tasktact.calculate_next_due_date(curr_due, tasktact.REC_DAILY, self.base_date)
        self.assertEqual(next_daily, "2026-10-15")

        next_weekly = tasktact.calculate_next_due_date(curr_due, tasktact.REC_WEEKLY, self.base_date)
        self.assertEqual(next_weekly, "2026-10-21")

        next_monthly = tasktact.calculate_next_due_date(curr_due, tasktact.REC_MONTHLY, self.base_date)
        self.assertEqual(next_monthly, "2026-11-14")

    def test_recurring_task_done_creates_next(self):
        """Test that when a recurring task is completed, next occurrence is generated."""
        tasks = [
            {
                "id": 1,
                "title": "Weekly Team Meeting",
                "category": "Work",
                "priority": "High",
                "due": "2026-10-14",
                "status": "Pending",
                "created": "2026-10-07 09:00:00",
                "completed": "",
                "notes": "Discuss roadmap",
                "tags": ["meeting"],
                "recurrence": "weekly",
            }
        ]
        
        # Simulate marking done
        task = tasks[0]
        task["status"] = tasktact.STATUS_DONE
        task["completed"] = "2026-10-14 10:00:00"
        
        next_due = tasktact.calculate_next_due_date(task["due"], task["recurrence"], self.base_date)
        new_id = tasktact.get_next_task_id(tasks)
        new_task = {
            "id": new_id,
            "title": task["title"],
            "category": task["category"],
            "priority": task["priority"],
            "due": next_due,
            "status": tasktact.STATUS_PENDING,
            "created": "2026-10-14 10:00:00",
            "completed": "",
            "notes": task["notes"],
            "tags": list(task["tags"]),
            "recurrence": task["recurrence"],
        }
        tasks.append(new_task)
        
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["status"], "Done")
        self.assertEqual(tasks[1]["id"], 2)
        self.assertEqual(tasks[1]["status"], "Pending")
        self.assertEqual(tasks[1]["due"], "2026-10-21")

    # -------------------------------------------------------------------------
    # 4. Overdue & Due Filters (Core 2)
    # -------------------------------------------------------------------------
    def test_overdue_and_due_logic(self):
        """Test overdue, due today, and due this week boolean helpers."""
        # Overdue if pending and due before base_date
        self.assertTrue(tasktact.is_overdue("2026-10-13", tasktact.STATUS_PENDING, self.base_date))
        # Not overdue if status is Done
        self.assertFalse(tasktact.is_overdue("2026-10-13", tasktact.STATUS_DONE, self.base_date))
        # Not overdue if due today or in the future
        self.assertFalse(tasktact.is_overdue("2026-10-14", tasktact.STATUS_PENDING, self.base_date))
        self.assertFalse(tasktact.is_overdue("2026-10-15", tasktact.STATUS_PENDING, self.base_date))

        # Due today
        self.assertTrue(tasktact.is_due_today("2026-10-14", tasktact.STATUS_PENDING, self.base_date))
        self.assertFalse(tasktact.is_due_today("2026-10-15", tasktact.STATUS_PENDING, self.base_date))

        # Due this week (Week of Oct 12 - Oct 18, 2026)
        self.assertTrue(tasktact.is_due_this_week("2026-10-16", tasktact.STATUS_PENDING, self.base_date, "Monday"))
        self.assertFalse(tasktact.is_due_this_week("2026-10-25", tasktact.STATUS_PENDING, self.base_date, "Monday"))

    # -------------------------------------------------------------------------
    # 5. Safe Atomic Persistence & Backup Recovery (Upgrade G)
    # -------------------------------------------------------------------------
    def test_safe_save_and_backup(self):
        """Test atomic writing and automatic creation of tasks.json.bak."""
        initial_tasks = [{"id": 1, "title": "Version 1", "status": "Pending"}]
        updated_tasks = [{"id": 1, "title": "Version 2", "status": "Done"}]

        # First save creates primary file
        success1 = tasktact.safe_save_tasks(initial_tasks, self.tasks_file, self.backup_file)
        self.assertTrue(success1)
        self.assertTrue(os.path.exists(self.tasks_file))

        # Second save must back up Version 1 into backup file and write Version 2 to primary
        success2 = tasktact.safe_save_tasks(updated_tasks, self.tasks_file, self.backup_file)
        self.assertTrue(success2)
        self.assertTrue(os.path.exists(self.backup_file))

        loaded_primary = tasktact.load_tasks(self.tasks_file, self.backup_file)
        self.assertEqual(loaded_primary[0]["title"], "Version 2")

        with open(self.backup_file, "r", encoding="utf-8") as bf:
            backup_data = json.load(bf)
            self.assertEqual(backup_data[0]["title"], "Version 1")

    def test_corrupt_file_recovery_from_backup(self):
        """Test recovery from backup when the primary file is corrupted."""
        valid_tasks = [{"id": 42, "title": "Saved in Backup", "status": "Pending"}]
        # Initial save creates primary file; second save creates backup copy
        tasktact.safe_save_tasks(valid_tasks, self.tasks_file, self.backup_file)
        tasktact.safe_save_tasks(valid_tasks, self.tasks_file, self.backup_file)
        self.assertTrue(os.path.exists(self.backup_file))
        
        # Corrupt the primary file with broken JSON
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            f.write("{corrupt_json: [broken")

        # Load should detect corruption and restore from backup
        recovered = tasktact.load_tasks(self.tasks_file, self.backup_file)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["id"], 42)
        self.assertEqual(recovered[0]["title"], "Saved in Backup")

    # -------------------------------------------------------------------------
    # 6. Undo Mechanism (Upgrade D)
    # -------------------------------------------------------------------------
    def test_undo_action(self):
        """Test recording undo snapshots and rolling back actions."""
        tasks = [{"id": 1, "title": "Task 1", "status": "Pending"}]
        tasktact.record_undo_state("Add Task 2", tasks)

        # Mutate tasks
        tasks.append({"id": 2, "title": "Task 2", "status": "Pending"})
        self.assertEqual(len(tasks), 2)

        # Undo
        restored, action_desc = tasktact.perform_undo(tasks)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["title"], "Task 1")
        self.assertEqual(action_desc, "Add Task 2")

    # -------------------------------------------------------------------------
    # 7. Search & Filtering (Core 2, 6)
    # -------------------------------------------------------------------------
    def test_search_tasks(self):
        """Test keyword searching in title, category, notes, and tags."""
        tasks = [
            {"id": 1, "title": "Prepare Math Exam", "category": "Study", "notes": "", "tags": ["school"]},
            {"id": 2, "title": "Buy Groceries", "category": "Personal", "notes": "Need milk and eggs", "tags": ["shopping"]},
            {"id": 3, "title": "Submit Assignment", "category": "Academic", "notes": "", "tags": ["math", "urgent"]},
        ]
        
        # Search by title
        self.assertEqual(len(tasktact.search_tasks(tasks, "exam")), 1)
        # Search by notes
        self.assertEqual(len(tasktact.search_tasks(tasks, "milk")), 1)
        # Search by tag
        self.assertEqual(len(tasktact.search_tasks(tasks, "math")), 2)
        # Search case-insensitive
        self.assertEqual(len(tasktact.search_tasks(tasks, "PERSONAL")), 1)

    def test_filtering_and_sorting(self):
        """Test filtering by category/tag and sorting by priority/due date."""
        tasks = [
            {"id": 1, "title": "Task A", "category": "Work", "priority": "Low", "due": "2026-10-30", "status": "Pending", "created": "2026-10-01 10:00:00", "tags": ["alpha"]},
            {"id": 2, "title": "Task B", "category": "Personal", "priority": "High", "due": "2026-10-15", "status": "Pending", "created": "2026-10-02 10:00:00", "tags": ["beta"]},
            {"id": 3, "title": "Task C", "category": "Work", "priority": "Medium", "due": "", "status": "Done", "created": "2026-10-03 10:00:00", "tags": ["alpha"]},
        ]

        # Filter by category
        work_tasks = tasktact.filter_tasks(tasks, "category", "Work")
        self.assertEqual(len(work_tasks), 2)

        # Filter by tag
        alpha_tasks = tasktact.filter_tasks(tasks, "tag", "alpha")
        self.assertEqual(len(alpha_tasks), 2)

        # Sort by priority (High -> Medium -> Low)
        by_priority = tasktact.sort_tasks(tasks, "priority")
        self.assertEqual(by_priority[0]["priority"], "High")
        self.assertEqual(by_priority[1]["priority"], "Medium")
        self.assertEqual(by_priority[2]["priority"], "Low")

        # Sort by due date (empty dates at the end)
        by_due = tasktact.sort_tasks(tasks, "due")
        self.assertEqual(by_due[0]["id"], 2)  # Oct 15
        self.assertEqual(by_due[1]["id"], 1)  # Oct 30
        self.assertEqual(by_due[2]["id"], 3)  # Empty date last

    # -------------------------------------------------------------------------
    # 8. Productivity Stats & Streak Calculation (Upgrade I)
    # -------------------------------------------------------------------------
    def test_streak_calculation(self):
        """Test consecutive days completion streak logic."""
        # Tasks completed today (Oct 14) and yesterday (Oct 13)
        tasks = [
            {"id": 1, "status": "Done", "completed": "2026-10-14 12:00:00"},
            {"id": 2, "status": "Done", "completed": "2026-10-13 18:30:00"},
            {"id": 3, "status": "Done", "completed": "2026-10-12 09:15:00"},
            {"id": 4, "status": "Pending", "completed": ""},
        ]
        streak = tasktact.calculate_completion_streak(tasks, self.base_date)
        self.assertEqual(streak, 3)

        # Broken streak (no completion today or yesterday)
        broken_tasks = [
            {"id": 1, "status": "Done", "completed": "2026-10-10 12:00:00"},
        ]
        self.assertEqual(tasktact.calculate_completion_streak(broken_tasks, self.base_date), 0)

    # -------------------------------------------------------------------------
    # 9. Duplicate Detection (Upgrade J)
    # -------------------------------------------------------------------------
    def test_check_duplicate_title(self):
        """Test duplicate title warning detection."""
        tasks = [
            {"id": 1, "title": "Submit Report", "status": "Pending"},
            {"id": 2, "title": "Call Alice", "status": "Pending"},
        ]
        self.assertIsNotNone(tasktact.check_duplicate_title(tasks, "submit report"))
        self.assertIsNotNone(tasktact.check_duplicate_title(tasks, "SUBMIT REPORT"))
        self.assertIsNone(tasktact.check_duplicate_title(tasks, "Submit Report 2"))

    # -------------------------------------------------------------------------
    # 10. Data Exports: CSV and TXT Report (Core 9, Upgrade H)
    # -------------------------------------------------------------------------
    def test_export_txt_and_csv(self):
        """Test generating report.txt and exporting tasks to CSV."""
        tasks = [
            {
                "id": 1,
                "title": "Project Task",
                "category": "Work",
                "priority": "High",
                "due": "2026-10-20",
                "status": "Pending",
                "created": "2026-10-14 10:00:00",
                "completed": "",
                "notes": "Testing exports",
                "tags": ["unit", "test"],
                "recurrence": "none",
            }
        ]
        
        # Export text report
        txt_ok = tasktact.export_report_to_txt(tasks, self.report_file)
        self.assertTrue(txt_ok)
        self.assertTrue(os.path.exists(self.report_file))
        with open(self.report_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("TASKTACT PRODUCTIVITY & SUMMARY REPORT", content)
            self.assertIn("Total Tasks         : 1", content)
            
        # Export CSV
        csv_ok = tasktact.export_tasks_to_csv(tasks, self.csv_file)
        self.assertTrue(csv_ok)
        self.assertTrue(os.path.exists(self.csv_file))
        with open(self.csv_file, "r", encoding="utf-8") as f:
            header_line = f.readline().strip()
            self.assertIn("id,title,category,priority,due", header_line)
            self.assertIn("status,created,completed", header_line)
            data_line = f.readline().strip()
            self.assertIn("Project Task", data_line)
            self.assertIn("unit;test", data_line)


if __name__ == "__main__":
    unittest.main()
