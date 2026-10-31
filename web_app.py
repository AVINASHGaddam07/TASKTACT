"""
TaskTact - Web Application Server
=================================
A lightweight, zero-dependency HTTP server that connects TaskTact's
intelligent task management engine with an Apple iOS-styled glassmorphic web UI.

Includes:
- Multi-page navigation endpoints (Home, Tasks, Activity, Analytics)
- Real-time Activity Timeline tracking & persistence (activity.json)
- REST API for tasks, suggestions, statistics, and exports
"""

import json
import logging
import mimetypes
import os
import sys
import urllib.parse
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

# Import the core TaskTact business logic and constants
import tasktact

HOST = "127.0.0.1"
DEFAULT_PORT = 5000
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ACTIVITY_FILE = "activity.json"


# ============================================================================
# ACTIVITY TIMELINE LOGGING & PERSISTENCE
# ============================================================================

def load_activity_log() -> List[Dict[str, Any]]:
    """Load activity history from activity.json, or generate from existing tasks with thread safety."""
    with tasktact.STORAGE_LOCK:
        if os.path.exists(ACTIVITY_FILE):
            try:
                with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as err:
                logging.error(f"Error loading {ACTIVITY_FILE}: {err}")

        # Bootstrap activity history from existing tasks if activity.json is new
        tasks = tasktact.load_tasks()
        activities = []
        for t in tasks:
            if t.get("created"):
                activities.append({
                    "id": f"act_create_{t.get('id')}",
                    "type": "created",
                    "title": f"Created task: {t.get('title')}",
                    "task_id": t.get("id"),
                    "task_title": t.get("title"),
                    "category": t.get("category", "General"),
                    "priority": t.get("priority", "Medium"),
                    "timestamp": t.get("created"),
                })
            if t.get("completed") and t.get("status") == tasktact.STATUS_DONE:
                activities.append({
                    "id": f"act_done_{t.get('id')}",
                    "type": "completed",
                    "title": f"Completed task: {t.get('title')}",
                    "task_id": t.get("id"),
                    "task_title": t.get("title"),
                    "category": t.get("category", "General"),
                    "priority": t.get("priority", "Medium"),
                    "timestamp": t.get("completed"),
                })

        # Sort chronological descending (newest first)
        activities.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
        save_activity_log(activities)
        return activities


def save_activity_log(activities: List[Dict[str, Any]]) -> None:
    """Save up to the last 100 activity items to activity.json with thread safety."""
    with tasktact.STORAGE_LOCK:
        try:
            trimmed = activities[:100]
            with open(ACTIVITY_FILE, "w", encoding="utf-8") as f:
                json.dump(trimmed, f, indent=2, ensure_ascii=False)
        except Exception as err:
            logging.error(f"Error saving activity log: {err}")


def log_activity_event(action_type: str, title: str, task_dict: Optional[Dict[str, Any]] = None, details: str = "") -> None:
    """Append a new user activity event to the timeline."""
    with tasktact.STORAGE_LOCK:
        activities = load_activity_log()
        event_id = f"act_{int(datetime.now().timestamp() * 1000)}"
        now_str = datetime.now().strftime(tasktact.DATETIME_FORMAT)

        entry = {
            "id": event_id,
            "type": action_type,  # 'created', 'completed', 'updated', 'deleted', 'recurring', 'undone', 'cleared'
            "title": title,
            "details": details,
            "timestamp": now_str,
        }

        if task_dict:
            entry["task_id"] = task_dict.get("id")
            entry["task_title"] = task_dict.get("title")
            entry["category"] = task_dict.get("category", "General")
            entry["priority"] = task_dict.get("priority", "Medium")

        activities.insert(0, entry)
        save_activity_log(activities)


# ============================================================================
# HTTP REQUEST HANDLER
# ============================================================================

class TaskTactRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler providing REST API endpoints and serving static UI files."""

    def log_message(self, format: str, *args: Any) -> None:
        """Redirect server logs to tasktact.log rather than stderr."""
        logging.info("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))

    def send_json_response(self, data: Any, status_code: int = 200) -> None:
        """Send a formatted JSON response with CORS headers."""
        response_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(response_bytes)

    def send_error_response(self, error_message: str, status_code: int = 400) -> None:
        """Send a structured error response."""
        self.send_json_response({"success": False, "error": error_message}, status_code=status_code)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ------------------------------------------------------------------------
    # GET REQUEST ROUTING
    # ------------------------------------------------------------------------
    def do_GET(self) -> None:
        """Route GET requests to API handlers or static file server."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # API Routes
        if path == "/api/tasks":
            self.handle_get_tasks(query_params)
        elif path == "/api/ongoing-task":
            self.handle_get_ongoing_task()
        elif path == "/api/suggestions":
            self.handle_get_suggestions()
        elif path == "/api/stats":
            self.handle_get_stats()
        elif path == "/api/activity":
            self.handle_get_activity()
        elif path == "/api/config":
            self.handle_get_config()
        elif path == "/api/health":
            self.handle_get_health()
        elif path == "/api/export/csv":
            self.handle_export_csv()
        elif path == "/api/export/txt":
            self.handle_export_txt()
        else:
            self.serve_static_file(parsed_url.path)

    # ------------------------------------------------------------------------
    # POST REQUEST ROUTING
    # ------------------------------------------------------------------------
    def do_POST(self) -> None:
        """Route POST requests to API action handlers."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        body = self.read_json_body()

        if path == "/api/tasks":
            self.handle_create_task(body)
        elif path == "/api/ongoing-task" or (path.startswith("/api/tasks/") and path.endswith("/ongoing")):
            if path.startswith("/api/tasks/"):
                parts = path.strip("/").split("/")
                try:
                    body["task_id"] = int(parts[2])
                except (IndexError, ValueError):
                    pass
            self.handle_set_ongoing_task(body)
        elif path.startswith("/api/tasks/") and path.endswith("/done"):
            parts = path.strip("/").split("/")
            try:
                task_id = int(parts[2])
                self.handle_mark_done(task_id)
            except (IndexError, ValueError):
                self.send_error_response("Invalid task ID")
        elif path == "/api/undo":
            self.handle_undo()
        elif path == "/api/clear-completed":
            self.handle_clear_completed()
        elif path == "/api/parse-date":
            self.handle_parse_date(body)
        elif path == "/api/config":
            self.handle_update_config(body)
        else:
            self.send_error_response("Endpoint not found", status_code=404)

    # ------------------------------------------------------------------------
    # PUT REQUEST ROUTING
    # ------------------------------------------------------------------------
    def do_PUT(self) -> None:
        """Route PUT requests to update task fields."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        body = self.read_json_body()

        if path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            try:
                task_id = int(parts[2])
                self.handle_update_task(task_id, body)
            except (IndexError, ValueError):
                self.send_error_response("Invalid task ID")
        else:
            self.send_error_response("Endpoint not found", status_code=404)

    # ------------------------------------------------------------------------
    # DELETE REQUEST ROUTING
    # ------------------------------------------------------------------------
    def do_DELETE(self) -> None:
        """Route DELETE requests to remove tasks."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        if path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            try:
                task_id = int(parts[2])
                self.handle_delete_task(task_id)
            except (IndexError, ValueError):
                self.send_error_response("Invalid task ID")
        else:
            self.send_error_response("Endpoint not found", status_code=404)

    # ------------------------------------------------------------------------
    # HELPER: READ JSON REQUEST BODY
    # ------------------------------------------------------------------------
    def read_json_body(self) -> Dict[str, Any]:
        """Read and parse incoming JSON payload from request."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                raw_bytes = self.rfile.read(content_length)
                return json.loads(raw_bytes.decode("utf-8"))
        except Exception as err:
            logging.error(f"Error reading JSON request body: {err}")
        return {}

    # ------------------------------------------------------------------------
    # STATIC FILE SERVER
    # ------------------------------------------------------------------------
    def serve_static_file(self, req_path: str) -> None:
        """Serve HTML, CSS, JS, and asset files with correct MIME types."""
        clean_path = req_path.strip("/")
        if not clean_path:
            clean_path = "index.html"

        file_path = os.path.normpath(os.path.join(WEB_DIR, clean_path))
        if not file_path.startswith(WEB_DIR) or not os.path.exists(file_path) or os.path.isdir(file_path):
            file_path = os.path.join(WEB_DIR, "index.html")

        if not os.path.exists(file_path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found: Static file missing.")
            return

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if "text" in mime_type or "javascript" in mime_type else mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as err:
            logging.error(f"Error serving static file {file_path}: {err}")
            self.send_response(500)
            self.end_headers()

    # ------------------------------------------------------------------------
    # API HANDLERS
    # ------------------------------------------------------------------------
    def handle_get_health(self) -> None:
        """System health and diagnostic telemetry endpoint."""
        with tasktact.STORAGE_LOCK:
            tasks = tasktact.load_tasks()
            db_size = os.path.getsize(tasktact.TASKS_FILE) if os.path.exists(tasktact.TASKS_FILE) else 0
            pending_cnt = sum(1 for t in tasks if t.get("status") == tasktact.STATUS_PENDING)
            done_cnt = sum(1 for t in tasks if t.get("status") == tasktact.STATUS_DONE)
            
            health_data = {
                "status": "healthy",
                "version": "2.0.0-pro",
                "timestamp": datetime.now().strftime(tasktact.DATETIME_FORMAT),
                "tasks_total": len(tasks),
                "tasks_pending": pending_cnt,
                "tasks_completed": done_cnt,
                "database_size_bytes": db_size,
                "storage_engine": "JSON-SafeAtomic+RLock",
                "system_platform": sys.platform,
                "python_version": sys.version.split()[0]
            }
            self.send_json_response({"success": True, "health": health_data})

    def handle_get_ongoing_task(self) -> None:
        """Fetch the currently active / ongoing task."""
        tasks = tasktact.load_tasks()
        config = tasktact.load_config()
        ongoing_id = config.get("ongoing_task_id")
        try:
            ongoing_id = int(ongoing_id) if ongoing_id is not None else None
        except (ValueError, TypeError):
            ongoing_id = None

        ongoing_task = None
        if ongoing_id:
            ongoing_task = tasktact.find_task_by_id(tasks, ongoing_id)
            if ongoing_task and ongoing_task.get("status") == tasktact.STATUS_DONE:
                ongoing_task = None

        # Fallback to the top smart suggestion or first pending task
        if not ongoing_task:
            suggestions = tasktact.get_smart_suggestions(tasks, top_n=1)
            if suggestions:
                ongoing_task = suggestions[0][0]
                config["ongoing_task_id"] = ongoing_task.get("id")
                tasktact.save_config(config)
            else:
                pending = [t for t in tasks if t.get("status") == tasktact.STATUS_PENDING]
                if pending:
                    ongoing_task = pending[0]
                    config["ongoing_task_id"] = ongoing_task.get("id")
                    tasktact.save_config(config)

        self.send_json_response({"success": True, "ongoing_task": ongoing_task})

    def handle_set_ongoing_task(self, body: Dict[str, Any]) -> None:
        """Set a specific task as the active ongoing task."""
        raw_id = body.get("task_id")
        if raw_id is None:
            raw_id = body.get("id")
        try:
            task_id = int(raw_id) if raw_id is not None else None
        except (ValueError, TypeError):
            task_id = None

        tasks = tasktact.load_tasks()
        task = tasktact.find_task_by_id(tasks, task_id) if task_id else None

        # If specific task not found, pick first pending task
        if not task and tasks:
            pending = [t for t in tasks if t.get("status") == tasktact.STATUS_PENDING]
            if pending:
                task = pending[0]
                task_id = task.get("id")

        config = tasktact.load_config()
        config["ongoing_task_id"] = task_id
        tasktact.save_config(config)

        if task:
            log_activity_event(
                action_type="updated",
                title=f"Focused on #{task['id']}: {task['title']}",
                task_dict=task,
                details=f"Set as active ongoing task (Priority: {task.get('priority')})"
            )

        self.send_json_response({
            "success": True,
            "ongoing_task": task,
            "message": f"Active task set to #{task['id']}: '{task['title']}'" if task else "Ongoing task updated!"
        })

    def handle_get_tasks(self, query_params: Dict[str, List[str]]) -> None:
        """Fetch tasks with filtering and sorting options."""
        tasks = tasktact.load_tasks()
        config = tasktact.load_config()
        ongoing_id = config.get("ongoing_task_id")
        try:
            ongoing_id = int(ongoing_id) if ongoing_id is not None else None
        except (ValueError, TypeError):
            ongoing_id = None

        filter_type = query_params.get("filter", ["all"])[0]
        filter_arg = query_params.get("arg", [""])[0]
        search_query = query_params.get("q", [""])[0]
        sort_by = query_params.get("sort", ["id"])[0]

        if search_query:
            tasks = tasktact.search_tasks(tasks, search_query)

        tasks = tasktact.filter_tasks(tasks, filter_type, filter_arg, config)
        tasks = tasktact.sort_tasks(tasks, sort_by)

        decorated = []
        today = date.today()
        for t in tasks:
            item = dict(t)
            item["is_overdue"] = tasktact.is_overdue(t.get("due", ""), t.get("status", ""), today)
            item["is_due_today"] = tasktact.is_due_today(t.get("due", ""), t.get("status", ""), today)
            item["is_ongoing"] = (t.get("id") == ongoing_id)
            decorated.append(item)

        self.send_json_response({"success": True, "tasks": decorated, "count": len(decorated), "ongoing_id": ongoing_id})

    def handle_get_suggestions(self) -> None:
        """Return smart suggestions (top 5 pending tasks with scores and rationale)."""
        tasks = tasktact.load_tasks()
        suggestions_raw = tasktact.get_smart_suggestions(tasks, top_n=5)
        suggestions = []
        for rank, (task, score, reason) in enumerate(suggestions_raw, 1):
            suggestions.append({
                "rank": rank,
                "task": task,
                "score": score,
                "reason": reason
            })
        self.send_json_response({"success": True, "suggestions": suggestions})

    def handle_get_stats(self) -> None:
        """Return aggregate summary metrics and productivity stats."""
        tasks = tasktact.load_tasks()
        summary = tasktact.generate_summary_data(tasks)
        self.send_json_response({"success": True, "stats": summary})

    def handle_get_activity(self) -> None:
        """Return chronological activity timeline and last activity summary."""
        activities = load_activity_log()
        last_activity = activities[0] if activities else None

        today_str = date.today().strftime(tasktact.DATE_FORMAT)
        today_actions_count = sum(1 for a in activities if str(a.get("timestamp", "")).startswith(today_str))

        self.send_json_response({
            "success": True,
            "activities": activities,
            "total_count": len(activities),
            "today_count": today_actions_count,
            "last_activity": last_activity,
        })

    def handle_get_config(self) -> None:
        """Return current application configuration."""
        config = tasktact.load_config()
        self.send_json_response({"success": True, "config": config})

    def handle_update_config(self, body: Dict[str, Any]) -> None:
        """Update and save application configuration."""
        config = tasktact.load_config()
        config.update(body)
        tasktact.save_config(config)
        self.send_json_response({"success": True, "config": config})

    def handle_parse_date(self, body: Dict[str, Any]) -> None:
        """Utility endpoint to parse natural language date strings."""
        raw_date = body.get("input", "")
        try:
            parsed = tasktact.parse_date_input(raw_date)
            self.send_json_response({"success": True, "parsed": parsed})
        except ValueError as err:
            self.send_error_response(str(err))

    def handle_create_task(self, body: Dict[str, Any]) -> None:
        """Create a new task with validation, duplicate detection, and activity logging."""
        title = body.get("title", "").strip()
        if not title:
            self.send_error_response("Task title is required.")
            return

        tasks = tasktact.load_tasks()
        config = tasktact.load_config()

        allow_duplicate = body.get("allow_duplicate", False)
        duplicate = tasktact.check_duplicate_title(tasks, title)
        if duplicate and not allow_duplicate:
            self.send_json_response({
                "success": False,
                "warning": True,
                "is_duplicate": True,
                "existing_task": duplicate,
                "message": f"A task with title '{duplicate['title']}' already exists (ID #{duplicate['id']})."
            })
            return

        category = body.get("category", "").strip() or config.get("default_category", "General")
        priority = body.get("priority", "").capitalize()
        if priority not in tasktact.VALID_PRIORITIES:
            priority = config.get("default_priority", tasktact.PRIORITY_MEDIUM)

        raw_due = body.get("due", "").strip()
        due_date = ""
        if raw_due:
            try:
                due_date = tasktact.parse_date_input(raw_due)
            except ValueError as e:
                self.send_error_response(str(e))
                return

        due_time = body.get("due_time", "").strip()
        alert_offset = body.get("alert_offset", "at_due")

        recurrence = body.get("recurrence", tasktact.REC_NONE)
        if recurrence not in tasktact.VALID_RECURRENCES:
            recurrence = tasktact.REC_NONE

        notes = body.get("notes", "").strip()
        tags = body.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        tasktact.record_undo_state(f"Add task '{title}'", tasks)

        new_task = {
            "id": tasktact.get_next_task_id(tasks),
            "title": title,
            "category": category,
            "priority": priority,
            "due": due_date,
            "due_time": due_time,
            "alert_offset": alert_offset,
            "status": tasktact.STATUS_PENDING,
            "created": datetime.now().strftime(tasktact.DATETIME_FORMAT),
            "completed": "",
            "notes": notes,
            "tags": tags,
            "recurrence": recurrence,
        }

        tasks.append(new_task)
        tasktact.safe_save_tasks(tasks)

        log_activity_event(
            action_type="created",
            title=f"Created #{new_task['id']}: {title}",
            task_dict=new_task,
            details=f"Priority: {priority} | Due: {due_date or 'No date'} | Category: {category}"
        )

        self.send_json_response({"success": True, "task": new_task, "message": f"Task #{new_task['id']} added!"})

    def handle_update_task(self, task_id: int, body: Dict[str, Any]) -> None:
        """Update attributes of an existing task and record timeline event."""
        tasks = tasktact.load_tasks()
        task = tasktact.find_task_by_id(tasks, task_id)
        if not task:
            self.send_error_response(f"Task #{task_id} not found.", status_code=404)
            return

        tasktact.record_undo_state(f"Update task #{task_id}", tasks)

        if "title" in body and body["title"].strip():
            task["title"] = body["title"].strip()
        if "category" in body:
            task["category"] = body["category"].strip() or "General"
        if "priority" in body and body["priority"].capitalize() in tasktact.VALID_PRIORITIES:
            task["priority"] = body["priority"].capitalize()
        if "due" in body:
            raw_due = body["due"].strip()
            if not raw_due or raw_due.lower() == "clear":
                task["due"] = ""
            else:
                try:
                    task["due"] = tasktact.parse_date_input(raw_due)
                except ValueError as err:
                    self.send_error_response(str(err))
                    return
        if "notes" in body:
            task["notes"] = body["notes"].strip()
        if "tags" in body:
            tags = body["tags"]
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            task["tags"] = tags
        if "recurrence" in body and body["recurrence"] in tasktact.VALID_RECURRENCES:
            task["recurrence"] = body["recurrence"]
        if "due_time" in body:
            task["due_time"] = body["due_time"].strip() if isinstance(body["due_time"], str) else ""
        if "alert_offset" in body:
            task["alert_offset"] = body["alert_offset"].strip() if isinstance(body["alert_offset"], str) else "at_due"

        tasktact.safe_save_tasks(tasks)

        log_activity_event(
            action_type="updated",
            title=f"Updated #{task_id}: {task['title']}",
            task_dict=task,
            details=f"Category: {task['category']} | Priority: {task['priority']} | Due: {task['due'] or 'none'}"
        )

        self.send_json_response({"success": True, "task": task, "message": f"Task #{task_id} updated."})

    def handle_mark_done(self, task_id: int) -> None:
        """Toggle task between Done and Pending, handle recurrence, and log timeline event."""
        tasks = tasktact.load_tasks()
        task = tasktact.find_task_by_id(tasks, task_id)
        if not task:
            self.send_error_response(f"Task #{task_id} not found.", status_code=404)
            return

        tasktact.record_undo_state(f"Toggle done task #{task_id}", tasks)
        now_str = datetime.now().strftime(tasktact.DATETIME_FORMAT)

        # Reopen task if already completed
        if task.get("status") == tasktact.STATUS_DONE:
            task["status"] = tasktact.STATUS_PENDING
            task["completed"] = ""
            tasktact.safe_save_tasks(tasks)

            log_activity_event(
                action_type="updated",
                title=f"Reopened #{task_id}: {task['title']}",
                task_dict=task,
                details="Task status returned to Pending"
            )

            self.send_json_response({
                "success": True,
                "task": task,
                "recurring_task": None,
                "message": f"Task #{task_id} reopened (marked Pending)."
            })
            return

        # Mark task as completed
        task["status"] = tasktact.STATUS_DONE
        task["completed"] = now_str

        log_activity_event(
            action_type="completed",
            title=f"Completed #{task_id}: {task['title']}",
            task_dict=task,
            details=f"Finished on {now_str}"
        )

        recurring_task = None
        recurrence = task.get("recurrence", tasktact.REC_NONE)
        if recurrence in (tasktact.REC_DAILY, tasktact.REC_WEEKLY, tasktact.REC_MONTHLY):
            next_due = tasktact.calculate_next_due_date(task.get("due", ""), recurrence)
            recurring_task = {
                "id": tasktact.get_next_task_id(tasks),
                "title": task["title"],
                "category": task.get("category", "General"),
                "priority": task.get("priority", tasktact.PRIORITY_MEDIUM),
                "due": next_due,
                "due_time": task.get("due_time", "") or "",
                "alert_offset": task.get("alert_offset", "at_due") or "at_due",
                "status": tasktact.STATUS_PENDING,
                "created": now_str,
                "completed": "",
                "notes": task.get("notes", ""),
                "tags": list(task.get("tags", [])),
                "recurrence": recurrence,
            }
            tasks.append(recurring_task)

            log_activity_event(
                action_type="recurring",
                title=f"Recurring task spawned: #{recurring_task['id']} {recurring_task['title']}",
                task_dict=recurring_task,
                details=f"Next recurrence due on {next_due} ({recurrence})"
            )

        # If completed task was active ongoing task, auto-advance ongoing ID
        config = tasktact.load_config()
        if config.get("ongoing_task_id") == task_id:
            if recurring_task:
                config["ongoing_task_id"] = recurring_task["id"]
            else:
                pending = [t for t in tasks if t.get("status") == tasktact.STATUS_PENDING and t.get("id") != task_id]
                config["ongoing_task_id"] = pending[0]["id"] if pending else None
            tasktact.save_config(config)

        tasktact.safe_save_tasks(tasks)
        self.send_json_response({
            "success": True,
            "task": task,
            "recurring_task": recurring_task,
            "message": f"Task #{task_id} marked as Done!"
        })

    def handle_delete_task(self, task_id: int) -> None:
        """Delete task with undo preservation and timeline logging."""
        tasks = tasktact.load_tasks()
        task = tasktact.find_task_by_id(tasks, task_id)
        if not task:
            self.send_error_response(f"Task #{task_id} not found.", status_code=404)
            return

        task_copy = dict(task)
        tasktact.record_undo_state(f"Delete task #{task_id} '{task['title']}'", tasks)
        tasks.remove(task)

        # Clear ongoing ID if the deleted task was ongoing
        config = tasktact.load_config()
        if config.get("ongoing_task_id") == task_id:
            pending = [t for t in tasks if t.get("status") == tasktact.STATUS_PENDING and t.get("id") != task_id]
            config["ongoing_task_id"] = pending[0]["id"] if pending else None
            tasktact.save_config(config)

        tasktact.safe_save_tasks(tasks)

        log_activity_event(
            action_type="deleted",
            title=f"Deleted #{task_id}: {task_copy['title']}",
            task_dict=task_copy,
            details=f"Removed from list"
        )

        self.send_json_response({"success": True, "message": f"Task #{task_id} deleted."})

    def handle_undo(self) -> None:
        """Revert the last mutation from the undo stack and record timeline event."""
        tasks = tasktact.load_tasks()
        restored, action_desc = tasktact.perform_undo(tasks)
        if action_desc:
            tasktact.safe_save_tasks(restored)

            log_activity_event(
                action_type="undone",
                title=f"Undid action: {action_desc}",
                details="Restored to prior state"
            )

            self.send_json_response({
                "success": True,
                "restored": True,
                "message": f"Undid: {action_desc}"
            })
        else:
            self.send_json_response({
                "success": True,
                "restored": False,
                "message": "Nothing to undo."
            })

    def handle_clear_completed(self) -> None:
        """Remove all completed tasks and log timeline event."""
        tasks = tasktact.load_tasks()
        completed_count = sum(1 for t in tasks if t.get("status") == tasktact.STATUS_DONE)
        if completed_count == 0:
            self.send_json_response({"success": True, "cleared_count": 0, "message": "No completed tasks to clear."})
            return

        tasktact.record_undo_state(f"Clear {completed_count} completed task(s)", tasks)
        tasks[:] = [t for t in tasks if t.get("status") != tasktact.STATUS_DONE]
        tasktact.safe_save_tasks(tasks)

        log_activity_event(
            action_type="cleared",
            title=f"Cleared {completed_count} completed task(s)",
            details="All finished tasks removed"
        )

        self.send_json_response({
            "success": True,
            "cleared_count": completed_count,
            "message": f"Cleared {completed_count} completed task(s)."
        })

    def handle_export_csv(self) -> None:
        """Trigger CSV export and deliver file content directly."""
        tasks = tasktact.load_tasks()
        export_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), tasktact.CSV_EXPORT_FILE)
        tasktact.export_tasks_to_csv(tasks, export_path)

        with open(export_path, "rb") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename={tasktact.CSV_EXPORT_FILE}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_export_txt(self) -> None:
        """Trigger text report export and deliver file content."""
        tasks = tasktact.load_tasks()
        export_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), tasktact.REPORT_FILE)
        tasktact.export_report_to_txt(tasks, export_path)

        with open(export_path, "rb") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename={tasktact.REPORT_FILE}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(port: int = DEFAULT_PORT) -> None:
    """Launch the ThreadingHTTPServer and open the default browser."""
    os.makedirs(WEB_DIR, exist_ok=True)
    load_activity_log()

    server = None
    for p in range(port, port + 10):
        try:
            server = ReusableThreadingHTTPServer((HOST, p), TaskTactRequestHandler)
            port = p
            break
        except OSError:
            continue

    if not server:
        print(f"Error: Could not bind to any port from {port} to {port + 10}.")
        sys.exit(1)

    url = f"http://{HOST}:{port}"
    print("=" * 65)
    print("      TASKTACT - APPLE iOS GLASSMORPHIC WEB INTERFACE      ")
    print("=" * 65)
    print(f"  * Web Server running at: {url}")
    print(f"  * Data synchronized with: tasks.json & activity.json")
    print("  * Press Ctrl+C in this terminal to shut down.")
    print("=" * 65)

    try:
        webbrowser.open(url)
    except Exception as err:
        logging.debug(f"Could not open browser automatically: {err}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down TaskTact Web Server. Goodbye!")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
