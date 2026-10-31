"""
TaskTact - Intelligent To-Do Manager
====================================
An innovative, modular Python console application for smart task management.
Built exclusively with Python standard library modules.

Author: TaskTact Team
Version: 1.0.0
"""

import copy
import csv
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple, Union

# Global Re-entrant Storage Lock for Thread-Safe Concurrency
STORAGE_LOCK = threading.RLock()

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

TASKS_FILE = "tasks.json"
BACKUP_FILE = "tasks.json.bak"
CONFIG_FILE = "config.json"
REPORT_FILE = "report.txt"
CSV_EXPORT_FILE = "tasks_export.csv"
LOG_FILE = "tasktact.log"

# Task Priorities
PRIORITY_HIGH = "High"
PRIORITY_MEDIUM = "Medium"
PRIORITY_LOW = "Low"
VALID_PRIORITIES = [PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW]
PRIORITY_WEIGHTS = {
    PRIORITY_HIGH: 30,
    PRIORITY_MEDIUM: 20,
    PRIORITY_LOW: 10,
}

# Task Statuses
STATUS_PENDING = "Pending"
STATUS_DONE = "Done"
VALID_STATUSES = [STATUS_PENDING, STATUS_DONE]

# Task Recurrence Options
REC_NONE = "none"
REC_DAILY = "daily"
REC_WEEKLY = "weekly"
REC_MONTHLY = "monthly"
VALID_RECURRENCES = [REC_NONE, REC_DAILY, REC_WEEKLY, REC_MONTHLY]

# Date & Time Formatting
DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# ANSI Color Codes
COLOR_RESET = "\033[0m"
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_MAGENTA = "\033[95m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"
COLOR_DIM = "\033[2m"

# Default Application Settings
DEFAULT_CONFIG = {
    "default_priority": PRIORITY_MEDIUM,
    "default_category": "General",
    "week_start_day": "Monday",  # "Monday" or "Sunday"
    "enable_color": True,
}

# Setup Error Logging with Automatic Rotation (Max 2MB per file, 3 backups)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
    rf_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    rf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root_logger.addHandler(rf_handler)

# ============================================================================
# DOMAIN ENTITY MODEL (STRONGLY TYPED TASK DATACLASS)
# ============================================================================

class PriorityEnum(str, Enum):
    HIGH = PRIORITY_HIGH
    MEDIUM = PRIORITY_MEDIUM
    LOW = PRIORITY_LOW


class StatusEnum(str, Enum):
    PENDING = STATUS_PENDING
    DONE = STATUS_DONE


@dataclass
class Task:
    """Strongly-typed Domain Entity for a TaskTact Task item."""
    id: int
    title: str
    category: str = "General"
    priority: str = PRIORITY_MEDIUM
    due: str = ""
    due_time: str = ""
    alert_offset: str = "at_due"
    status: str = STATUS_PENDING
    created: str = ""
    completed: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    recurrence: str = REC_NONE

    def to_dict(self) -> Dict[str, Any]:
        """Convert Task entity into JSON-serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Factory method to instantiate a strongly-typed Task from a dictionary."""
        return cls(
            id=int(data.get("id", 0)),
            title=str(data.get("title", "")),
            category=str(data.get("category", "General")),
            priority=str(data.get("priority", PRIORITY_MEDIUM)),
            due=str(data.get("due", "") or ""),
            due_time=str(data.get("due_time", "") or ""),
            alert_offset=str(data.get("alert_offset", "at_due") or "at_due"),
            status=str(data.get("status", STATUS_PENDING)),
            created=str(data.get("created", "") or ""),
            completed=str(data.get("completed", "") or ""),
            notes=str(data.get("notes", "") or ""),
            tags=list(data.get("tags") or []),
            recurrence=str(data.get("recurrence", REC_NONE) or REC_NONE),
        )

# Global In-Memory Undo Stack
# Structure: List of (action_description, deepcopied_tasks_list)
undo_stack: List[Tuple[str, List[Dict[str, Any]]]] = []


# ============================================================================
# COLOR & DISPLAY UTILITIES
# ============================================================================

def init_terminal_colors() -> None:
    """Initialize Windows virtual terminal processing for ANSI colors if on Windows."""
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11; ENABLE_VIRTUAL_TERMINAL_PROCESSING = 4
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception as err:
            logging.debug(f"Failed to enable VT mode on Windows: {err}")


def supports_color(config: Optional[Dict[str, Any]] = None) -> bool:
    """
    Check if colored output is supported and enabled by user configuration.
    
    :param config: Application configuration dictionary.
    :return: True if ANSI color output is active, False otherwise.
    """
    if config is not None and not config.get("enable_color", True):
        return False
    # Check if stdout is an interactive terminal
    return sys.stdout.isatty()


def colorize(text: str, color_code: str, config: Optional[Dict[str, Any]] = None) -> str:
    """
    Wrap text in an ANSI color code if colors are enabled; otherwise return text as-is.
    
    :param text: Text string to style.
    :param color_code: ANSI escape code sequence.
    :param config: Application configuration dictionary.
    :return: Colorized string or plain string.
    """
    if supports_color(config):
        return f"{color_code}{text}{COLOR_RESET}"
    return text


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

def load_config() -> Dict[str, Any]:
    """
    Load application settings from config.json with thread-safety. If missing or invalid,
    fall back to DEFAULT_CONFIG and save it.
    
    :return: Configuration dictionary.
    """
    with STORAGE_LOCK:
        if not os.path.exists(CONFIG_FILE):
            save_config(DEFAULT_CONFIG)
            return copy.deepcopy(DEFAULT_CONFIG)
        
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                config = copy.deepcopy(DEFAULT_CONFIG)
                config.update(data)
                return config
        except Exception as err:
            logging.error(f"Error loading configuration: {err}")
            return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> bool:
    """
    Save application settings to config.json with thread-safety.
    
    :param config: Configuration dictionary to persist.
    :return: True on success, False on failure.
    """
    with STORAGE_LOCK:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as file:
                json.dump(config, file, indent=2)
            return True
        except Exception as err:
            logging.error(f"Error saving configuration: {err}")
            return False


# ============================================================================
# DATE & TIME HELPERS
# ============================================================================

def add_months(source_date: date, months: int) -> date:
    """
    Calculate a new date by adding a specified number of calendar months,
    capping day of month to avoid month overflow (e.g., Jan 31 + 1 month = Feb 28/29).
    
    :param source_date: Starting date.
    :param months: Number of months to add.
    :return: New date object.
    """
    month_index = source_date.month - 1 + months
    year = source_date.year + month_index // 12
    month = month_index % 12 + 1
    
    # Days in each month for target year
    is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    month_days = [31, 29 if is_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(source_date.day, month_days[month - 1])
    return date(year, month, day)


def parse_date_input(user_input: str, reference_date: Optional[date] = None) -> str:
    """
    Parse a flexible user date input into standard YYYY-MM-DD format.
    Accepts:
      - "" (empty string: returns "")
      - "today"
      - "tomorrow" or "tmrw"
      - "+Nd" (e.g., "+3d" -> 3 days from reference)
      - "+Nw" (e.g., "+2w" -> 14 days from reference)
      - "+Nm" (e.g., "+1m" -> 1 month from reference)
      - Weekday names (e.g., "mon", "monday", "fri", "friday" -> next occurrence)
      - Standard "YYYY-MM-DD"
    
    :param user_input: Raw input string from the user.
    :param reference_date: Optional base date (defaults to date.today()).
    :return: Formatted date string "YYYY-MM-DD" or "" if empty.
    :raises ValueError: If the date format is unrecognized or invalid.
    """
    if reference_date is None:
        reference_date = date.today()
    
    cleaned = user_input.strip().lower()
    if not cleaned:
        return ""
    
    if cleaned == "today":
        return reference_date.strftime(DATE_FORMAT)
    
    if cleaned in ("tomorrow", "tmrw"):
        return (reference_date + timedelta(days=1)).strftime(DATE_FORMAT)
    
    # Check for relative offsets like +3d, +2w, +1m
    offset_match = re.match(r"^\+(\d+)([dwm])$", cleaned)
    if offset_match:
        qty = int(offset_match.group(1))
        unit = offset_match.group(2)
        if unit == "d":
            target = reference_date + timedelta(days=qty)
        elif unit == "w":
            target = reference_date + timedelta(weeks=qty)
        elif unit == "m":
            target = add_months(reference_date, qty)
        return target.strftime(DATE_FORMAT)
    
    # Check for weekday names
    weekdays = {
        "mon": 0, "monday": 0,
        "tue": 1, "tues": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6,
    }
    if cleaned in weekdays:
        target_weekday = weekdays[cleaned]
        current_weekday = reference_date.weekday()
        days_ahead = (target_weekday - current_weekday) % 7
        if days_ahead == 0:
            days_ahead = 7  # Next week's occurrence
        target = reference_date + timedelta(days=days_ahead)
        return target.strftime(DATE_FORMAT)
    
    # Check for exact YYYY-MM-DD
    try:
        parsed = datetime.strptime(cleaned, DATE_FORMAT).date()
        return parsed.strftime(DATE_FORMAT)
    except ValueError:
        raise ValueError(
            f"Invalid date format: '{user_input}'. Use YYYY-MM-DD, 'today', 'tomorrow', '+3d', '+2w', or weekday names."
        )


def calculate_next_due_date(current_due: str, recurrence: str, reference_date: Optional[date] = None) -> str:
    """
    Calculate the next occurrence due date for a recurring task.
    
    :param current_due: Existing due date string (YYYY-MM-DD) or empty.
    :param recurrence: Recurrence frequency ("daily", "weekly", "monthly").
    :param reference_date: Base date fallback if current_due is empty.
    :return: New due date string "YYYY-MM-DD".
    """
    if reference_date is None:
        reference_date = date.today()
    
    if current_due:
        try:
            base_date = datetime.strptime(current_due, DATE_FORMAT).date()
        except ValueError:
            base_date = reference_date
    else:
        base_date = reference_date
    
    if recurrence == REC_DAILY:
        next_date = base_date + timedelta(days=1)
    elif recurrence == REC_WEEKLY:
        next_date = base_date + timedelta(weeks=1)
    elif recurrence == REC_MONTHLY:
        next_date = add_months(base_date, 1)
    else:
        next_date = base_date
    
    return next_date.strftime(DATE_FORMAT)


def is_overdue(due_date_str: str, status: str, reference_date: Optional[date] = None) -> bool:
    """
    Determine if a task is overdue (pending and due before today).
    
    :param due_date_str: Task due date string.
    :param status: Task status ("Pending" or "Done").
    :param reference_date: Base date (defaults to date.today()).
    :return: True if overdue, False otherwise.
    """
    if status != STATUS_PENDING or not due_date_str:
        return False
    if reference_date is None:
        reference_date = date.today()
    try:
        due_d = datetime.strptime(due_date_str, DATE_FORMAT).date()
        return due_d < reference_date
    except ValueError:
        return False


def is_due_today(due_date_str: str, status: str, reference_date: Optional[date] = None) -> bool:
    """
    Determine if a task is pending and due on the reference date.
    
    :param due_date_str: Task due date string.
    :param status: Task status.
    :param reference_date: Base date.
    :return: True if due today, False otherwise.
    """
    if status != STATUS_PENDING or not due_date_str:
        return False
    if reference_date is None:
        reference_date = date.today()
    try:
        due_d = datetime.strptime(due_date_str, DATE_FORMAT).date()
        return due_d == reference_date
    except ValueError:
        return False


def is_due_this_week(due_date_str: str, status: str, reference_date: Optional[date] = None, week_start_day: str = "Monday") -> bool:
    """
    Determine if a task is pending and due within the current calendar week.
    
    :param due_date_str: Task due date string.
    :param status: Task status.
    :param reference_date: Base date.
    :param week_start_day: "Monday" or "Sunday".
    :return: True if due this week, False otherwise.
    """
    if status != STATUS_PENDING or not due_date_str:
        return False
    if reference_date is None:
        reference_date = date.today()
    try:
        due_d = datetime.strptime(due_date_str, DATE_FORMAT).date()
    except ValueError:
        return False
    
    if week_start_day.lower() == "sunday":
        start_offset = (reference_date.weekday() + 1) % 7
    else:  # Monday default
        start_offset = reference_date.weekday()
    
    week_start = reference_date - timedelta(days=start_offset)
    week_end = week_start + timedelta(days=6)
    
    return week_start <= due_d <= week_end


# ============================================================================
# PERSISTENCE & SAFE SAVING (ATOMIC WRITE & BACKUP)
# ============================================================================

def safe_save_tasks(tasks: List[Dict[str, Any]], filepath: str = TASKS_FILE, backup_filepath: Optional[str] = None) -> bool:
    """
    Persist tasks atomically and thread-safely to disk:
      1. Write to a temporary file.
      2. If target exists, copy current version to backup.
      3. Atomically replace target with temp file.
    
    :param tasks: List of task dictionaries.
    :param filepath: Target JSON file path.
    :param backup_filepath: Backup JSON file path (defaults to filepath + '.bak').
    :return: True on success, False on error.
    """
    with STORAGE_LOCK:
        if backup_filepath is None:
            backup_filepath = BACKUP_FILE if filepath == TASKS_FILE else f"{filepath}.bak"
        
        target_dir = os.path.dirname(os.path.abspath(filepath)) or "."
        temp_file = None
        try:
            temp_file = tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8")
            temp_path = temp_file.name
            json.dump(tasks, temp_file, indent=2, ensure_ascii=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            
            # Create or update backup if original file exists
            if os.path.exists(filepath):
                shutil.copy2(filepath, backup_filepath)
            
            # Atomic replace
            os.replace(temp_path, filepath)
            return True
        except Exception as err:
            logging.error(f"Error during safe save to {filepath}: {err}")
            if temp_file is not None and os.path.exists(temp_file.name):
                try:
                    os.remove(temp_file.name)
                except OSError:
                    pass
            return False


def load_tasks(filepath: str = TASKS_FILE, backup_filepath: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Load tasks from disk thread-safely. If the primary file is missing or corrupted,
    offer automatic restoration from the backup copy.
    
    :param filepath: Target JSON file path.
    :param backup_filepath: Backup JSON file path (defaults to filepath + '.bak').
    :return: List of task dictionaries.
    """
    with STORAGE_LOCK:
        if backup_filepath is None:
            backup_filepath = BACKUP_FILE if filepath == TASKS_FILE else f"{filepath}.bak"
            
        if not os.path.exists(filepath):
            # Primary file doesn't exist; check backup
            if os.path.exists(backup_filepath):
                try:
                    with open(backup_filepath, "r", encoding="utf-8") as b_file:
                        data = json.load(b_file)
                        logging.info("Primary tasks file missing; recovered from backup.")
                        return data
                except Exception as b_err:
                    logging.error(f"Failed to load from backup {backup_filepath}: {b_err}")
            return []
        
        try:
            with open(filepath, "r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            logging.error(f"Corrupted tasks file {filepath}: {err}")
            if os.path.exists(backup_filepath):
                print(f"\nWarning: '{filepath}' appears corrupted! Attempting recovery from backup '{backup_filepath}'...")
                try:
                    with open(backup_filepath, "r", encoding="utf-8") as b_file:
                        recovered = json.load(b_file)
                        # Restore primary file from backup
                        safe_save_tasks(recovered, filepath, backup_filepath)
                        print("Recovery successful! Restored tasks from backup.")
                        return recovered
                except Exception as b_err:
                    logging.error(f"Backup file is also corrupted: {b_err}")
                    print("Error: Backup file is also unreadable. Starting with empty tasks list.")
                    return []
            else:
                print(f"Error: '{filepath}' is corrupted and no backup was found. Starting with empty list.")
                return []


# ============================================================================
# UNDO HISTORY MANAGEMENT
# ============================================================================

def record_undo_state(action_description: str, tasks: List[Dict[str, Any]]) -> None:
    """
    Push a snapshot of the tasks state onto the undo stack before mutation.
    
    :param action_description: Human-readable description of the operation.
    :param tasks: Current tasks list to snapshot.
    """
    global undo_stack
    undo_stack.append((action_description, copy.deepcopy(tasks)))
    # Cap undo stack to last 20 operations to conserve memory
    if len(undo_stack) > 20:
        undo_stack.pop(0)


def perform_undo(current_tasks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Revert tasks to the most recent state from the undo stack.
    
    :param current_tasks: Active tasks list.
    :return: Tuple of (restored_tasks_list, action_description_or_None).
    """
    global undo_stack
    if not undo_stack:
        return current_tasks, None
    action_desc, previous_tasks = undo_stack.pop()
    return previous_tasks, action_desc


# ============================================================================
# TASK SCORING & INTELLIGENT SUGGESTIONS
# ============================================================================

def score_task(task: Dict[str, Any], reference_date: Optional[date] = None) -> Tuple[int, str]:
    """
    Calculate an intelligent priority score for a pending task.
    
    Scoring algorithm:
      - Priority Weight: High = 30, Medium = 20, Low = 10
      - Urgency Points:
          * Overdue: 40 + min(days_overdue * 2, 20) pts
          * Due today: 35 pts
          * Due tomorrow: 25 pts
          * Due in 2-3 days: 18 pts
          * Due in 4-7 days: 10 pts
          * Future (>7 days): 5 pts
          * No due date: 0 pts
      - Age Bonus: 1 pt per day pending since creation (capped at 15 pts)
    
    :param task: Task dictionary.
    :param reference_date: Current date reference (defaults to date.today()).
    :return: Tuple of (total_score, reason_summary).
    """
    if reference_date is None:
        reference_date = date.today()
    
    priority = task.get("priority", PRIORITY_MEDIUM)
    pri_score = PRIORITY_WEIGHTS.get(priority, 20)
    
    urgency_score = 0
    urgency_reason = "No due date"
    due_str = task.get("due", "")
    
    if due_str:
        try:
            due_d = datetime.strptime(due_str, DATE_FORMAT).date()
            delta_days = (due_d - reference_date).days
            
            if delta_days < 0:
                overdue_days = abs(delta_days)
                urgency_score = 40 + min(overdue_days * 2, 20)
                urgency_reason = f"Overdue by {overdue_days} day{'s' if overdue_days > 1 else ''}"
            elif delta_days == 0:
                urgency_score = 35
                urgency_reason = "Due today"
            elif delta_days == 1:
                urgency_score = 25
                urgency_reason = "Due tomorrow"
            elif 2 <= delta_days <= 3:
                urgency_score = 18
                urgency_reason = f"Due in {delta_days} days"
            elif 4 <= delta_days <= 7:
                urgency_score = 10
                urgency_reason = f"Due in {delta_days} days"
            else:
                urgency_score = 5
                urgency_reason = f"Due in {delta_days} days"
        except ValueError:
            urgency_score = 0
            urgency_reason = "Invalid due date"
    
    # Age bonus (tasks pending for a long time get attention)
    age_bonus = 0
    created_str = task.get("created", "")
    if created_str:
        try:
            created_dt = datetime.strptime(created_str, DATETIME_FORMAT).date()
            days_old = max(0, (reference_date - created_dt).days)
            age_bonus = min(days_old, 15)
        except ValueError:
            age_bonus = 0
    
    total_score = pri_score + urgency_score + age_bonus
    reason = f"{priority} priority, {urgency_reason}"
    if age_bonus >= 5:
        reason += f" (+{age_bonus}d age bonus)"
    
    return total_score, reason


def get_smart_suggestions(tasks: List[Dict[str, Any]], top_n: int = 5, reference_date: Optional[date] = None) -> List[Tuple[Dict[str, Any], int, str]]:
    """
    Evaluate all pending tasks and return the top N scored tasks with rationale.
    
    :param tasks: List of all tasks.
    :param top_n: Number of recommendations to return.
    :param reference_date: Base date reference.
    :return: List of tuples (task_dict, score, reason).
    """
    pending_tasks = [t for t in tasks if t.get("status") == STATUS_PENDING]
    scored: List[Tuple[Dict[str, Any], int, str]] = []
    
    for task in pending_tasks:
        score, reason = score_task(task, reference_date)
        scored.append((task, score, reason))
    
    # Sort descending by score; if tied, sort by earlier due date then ID
    def sort_key(item: Tuple[Dict[str, Any], int, str]):
        task_dict, score_val, _ = item
        due_val = task_dict.get("due") or "9999-99-99"
        return (-score_val, due_val, task_dict.get("id", 0))
    
    scored.sort(key=sort_key)
    return scored[:top_n]


# ============================================================================
# SEARCH, FILTERING & SORTING
# ============================================================================

def search_tasks(tasks: List[Dict[str, Any]], keyword: str) -> List[Dict[str, Any]]:
    """
    Search tasks matching a keyword across title, category, notes, or tags.
    
    :param tasks: List of tasks.
    :param keyword: Search query string.
    :return: List of matching task dictionaries.
    """
    kw = keyword.strip().lower()
    if not kw:
        return []
    
    results: List[Dict[str, Any]] = []
    for t in tasks:
        title = t.get("title", "").lower()
        category = t.get("category", "").lower()
        notes = t.get("notes", "").lower()
        tags = [str(tag).lower() for tag in t.get("tags", [])]
        
        if kw in title or kw in category or kw in notes or any(kw in tag for tag in tags):
            results.append(t)
    return results


def filter_tasks(tasks: List[Dict[str, Any]], filter_type: str, filter_arg: Optional[str] = None, config: Optional[Dict[str, Any]] = None, reference_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Filter tasks based on specified criteria.
    Options: 'all', 'pending', 'completed', 'overdue', 'due_today', 'due_this_week', 'category', 'tag'.
    
    :param tasks: List of tasks.
    :param filter_type: Filter criterion name.
    :param filter_arg: Additional argument (e.g., category name or tag string).
    :param config: Application configuration.
    :param reference_date: Base date.
    :return: Filtered list of tasks.
    """
    if reference_date is None:
        reference_date = date.today()
    
    ft = filter_type.lower()
    if ft == "all":
        return list(tasks)
    elif ft == "pending":
        return [t for t in tasks if t.get("status") == STATUS_PENDING]
    elif ft == "completed":
        return [t for t in tasks if t.get("status") == STATUS_DONE]
    elif ft == "overdue":
        return [t for t in tasks if is_overdue(t.get("due", ""), t.get("status", ""), reference_date)]
    elif ft == "due_today":
        return [t for t in tasks if is_due_today(t.get("due", ""), t.get("status", ""), reference_date)]
    elif ft == "due_this_week":
        week_start = "Monday"
        if config:
            week_start = config.get("week_start_day", "Monday")
        return [t for t in tasks if is_due_this_week(t.get("due", ""), t.get("status", ""), reference_date, week_start)]
    elif ft == "category":
        arg = (filter_arg or "").strip().lower()
        return [t for t in tasks if t.get("category", "").lower() == arg]
    elif ft == "tag":
        arg = (filter_arg or "").strip().lower()
        return [t for t in tasks if any(arg == str(tag).strip().lower() for tag in t.get("tags", []))]
    return list(tasks)


def sort_tasks(tasks: List[Dict[str, Any]], sort_key: str) -> List[Dict[str, Any]]:
    """
    Sort a list of tasks by a given criteria:
      - 'id': By task ID ascending
      - 'due': By due date (empty dates placed at the end)
      - 'priority': High -> Medium -> Low
      - 'category': Alphabetical A-Z
      - 'created': Creation date newest first
    
    :param tasks: Tasks list to sort.
    :param sort_key: Sorting mode string.
    :return: Newly sorted list of tasks.
    """
    pri_rank = {PRIORITY_HIGH: 1, PRIORITY_MEDIUM: 2, PRIORITY_LOW: 3}
    
    if sort_key == "due":
        return sorted(tasks, key=lambda t: (t.get("due") == "", t.get("due", ""), t.get("id", 0)))
    elif sort_key == "priority":
        return sorted(tasks, key=lambda t: (pri_rank.get(t.get("priority", PRIORITY_MEDIUM), 4), t.get("id", 0)))
    elif sort_key == "category":
        return sorted(tasks, key=lambda t: (t.get("category", "").lower(), t.get("id", 0)))
    elif sort_key == "created":
        return sorted(tasks, key=lambda t: t.get("created", ""), reverse=True)
    elif sort_key == "id":
        return sorted(tasks, key=lambda t: t.get("id", 0))
    return tasks


# ============================================================================
# PRODUCTIVITY METRICS & REPORTS
# ============================================================================

def calculate_completion_streak(tasks: List[Dict[str, Any]], reference_date: Optional[date] = None) -> int:
    """
    Calculate the current streak of consecutive days with at least one task completion.
    The streak remains alive if a task was completed today or yesterday.
    
    :param tasks: Tasks list.
    :param reference_date: Base date (defaults to date.today()).
    :return: Current streak length in days.
    """
    if reference_date is None:
        reference_date = date.today()
    
    completed_dates = set()
    for t in tasks:
        comp_str = t.get("completed", "")
        if t.get("status") == STATUS_DONE and comp_str:
            try:
                dt = datetime.strptime(comp_str, DATETIME_FORMAT).date()
                completed_dates.add(dt)
            except ValueError:
                try:
                    dt = datetime.strptime(comp_str, DATE_FORMAT).date()
                    completed_dates.add(dt)
                except ValueError:
                    pass
    
    if not completed_dates:
        return 0
    
    # Determine starting anchor (today or yesterday)
    streak = 0
    current_check = reference_date
    if current_check in completed_dates:
        streak += 1
        current_check -= timedelta(days=1)
    elif (current_check - timedelta(days=1)) in completed_dates:
        current_check -= timedelta(days=1)
        streak += 1
        current_check -= timedelta(days=1)
    else:
        return 0
    
    while current_check in completed_dates:
        streak += 1
        current_check -= timedelta(days=1)
    
    return streak


def generate_summary_data(tasks: List[Dict[str, Any]], reference_date: Optional[date] = None) -> Dict[str, Any]:
    """
    Compile comprehensive productivity statistics and task counts.
    
    :param tasks: List of tasks.
    :param reference_date: Base date reference.
    :return: Dictionary containing metrics and aggregations.
    """
    if reference_date is None:
        reference_date = date.today()
    
    total = len(tasks)
    completed = sum(1 for t in tasks if t.get("status") == STATUS_DONE)
    pending = sum(1 for t in tasks if t.get("status") == STATUS_PENDING)
    overdue = sum(1 for t in tasks if is_overdue(t.get("due", ""), t.get("status", ""), reference_date))
    completion_rate = (completed / total * 100.0) if total > 0 else 0.0
    
    # Pending priority breakdown
    priority_counts = {PRIORITY_HIGH: 0, PRIORITY_MEDIUM: 0, PRIORITY_LOW: 0}
    for t in tasks:
        if t.get("status") == STATUS_PENDING:
            p = t.get("priority", PRIORITY_MEDIUM)
            priority_counts[p] = priority_counts.get(p, 0) + 1
    
    # Category counts
    category_counts: Dict[str, Dict[str, int]] = {}
    for t in tasks:
        cat = t.get("category", "General") or "General"
        if cat not in category_counts:
            category_counts[cat] = {"total": 0, "pending": 0, "completed": 0}
        category_counts[cat]["total"] += 1
        if t.get("status") == STATUS_DONE:
            category_counts[cat]["completed"] += 1
        else:
            category_counts[cat]["pending"] += 1
    
    # Most-used category
    most_used_category = "None"
    max_cat_count = 0
    for cat, stats in category_counts.items():
        if stats["total"] > max_cat_count:
            max_cat_count = stats["total"]
            most_used_category = cat
    
    # Completed this week (past 7 days or calendar week)
    week_start = reference_date - timedelta(days=reference_date.weekday())
    completed_this_week = 0
    for t in tasks:
        comp_str = t.get("completed", "")
        if t.get("status") == STATUS_DONE and comp_str:
            try:
                comp_d = datetime.strptime(comp_str, DATETIME_FORMAT).date()
                if comp_d >= week_start:
                    completed_this_week += 1
            except ValueError:
                pass
    
    streak = calculate_completion_streak(tasks, reference_date)
    
    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "overdue": overdue,
        "completion_rate": completion_rate,
        "priority_counts": priority_counts,
        "category_counts": category_counts,
        "most_used_category": most_used_category,
        "completed_this_week": completed_this_week,
        "streak": streak,
        "reference_date": reference_date.strftime(DATE_FORMAT),
    }


def format_summary_report(summary: Dict[str, Any]) -> str:
    """
    Format summary statistics into a clean text report.
    
    :param summary: Summary metrics dictionary.
    :return: Multiline formatted report string.
    """
    lines = [
        "=" * 60,
        "             TASKTACT PRODUCTIVITY & SUMMARY REPORT        ",
        "=" * 60,
        f"Generated On          : {datetime.now().strftime(DATETIME_FORMAT)}",
        f"Reference Date        : {summary['reference_date']}",
        "-" * 60,
        "OVERVIEW METRICS",
        f"  Total Tasks         : {summary['total']}",
        f"  Completed Tasks     : {summary['completed']}",
        f"  Pending Tasks       : {summary['pending']}",
        f"  Overdue Tasks       : {summary['overdue']}",
        f"  Completion Rate     : {summary['completion_rate']:.1f}%",
        "-" * 60,
        "PRODUCTIVITY HIGHLIGHTS",
        f"  Current Streak      : {summary['streak']} day(s) with completions",
        f"  Completed This Week : {summary['completed_this_week']} task(s)",
        f"  Most Active Category: {summary['most_used_category']}",
        "-" * 60,
        "PENDING TASKS BY PRIORITY",
        f"  High Priority   [!] : {summary['priority_counts'].get(PRIORITY_HIGH, 0)}",
        f"  Medium Priority [*] : {summary['priority_counts'].get(PRIORITY_MEDIUM, 0)}",
        f"  Low Priority    [-] : {summary['priority_counts'].get(PRIORITY_LOW, 0)}",
        "-" * 60,
        "BREAKDOWN BY CATEGORY",
        f"  {'Category':<20} {'Total':<10} {'Pending':<10} {'Completed':<10}",
        f"  {'-'*18:<20} {'-'*8:<10} {'-'*8:<10} {'-'*8:<10}",
    ]
    
    if summary["category_counts"]:
        for cat, stats in sorted(summary["category_counts"].items()):
            lines.append(f"  {cat[:18]:<20} {stats['total']:<10} {stats['pending']:<10} {stats['completed']:<10}")
    else:
        lines.append("  (No tasks recorded)")
    
    lines.append("=" * 60)
    return "\n".join(lines)


def export_report_to_txt(tasks: List[Dict[str, Any]], filepath: str = REPORT_FILE) -> bool:
    """
    Export the summary report to a text file.
    
    :param tasks: Tasks list.
    :param filepath: Target file path.
    :return: True on success, False on error.
    """
    try:
        summary = generate_summary_data(tasks)
        report_text = format_summary_report(summary)
        with open(filepath, "w", encoding="utf-8") as file:
            file.write(report_text)
        return True
    except Exception as err:
        logging.error(f"Error exporting text report to {filepath}: {err}")
        return False


def export_tasks_to_csv(tasks: List[Dict[str, Any]], filepath: str = CSV_EXPORT_FILE) -> bool:
    """
    Export all tasks with complete details to a standard CSV file.
    
    :param tasks: Tasks list.
    :param filepath: Target CSV file path.
    :return: True on success, False on error.
    """
    fieldnames = [
        "id", "title", "category", "priority", "due", "due_time", "alert_offset",
        "status", "created", "completed", "notes", "tags", "recurrence"
    ]
    try:
        with open(filepath, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for t in tasks:
                row = dict(t)
                if isinstance(row.get("tags"), list):
                    row["tags"] = ";".join(str(tag) for tag in row["tags"])
                writer.writerow(row)
        return True
    except Exception as err:
        logging.error(f"Error exporting tasks to CSV {filepath}: {err}")
        return False


# ============================================================================
# TABLE RENDERING & CLI PRESENTATION
# ============================================================================

def format_task_table(tasks: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None, reference_date: Optional[date] = None) -> str:
    """
    Render a clean, aligned, color-coded ASCII table of tasks.
    
    :param tasks: Tasks list to format.
    :param config: Configuration dict.
    :param reference_date: Base date reference.
    :return: Aligned multiline table string.
    """
    if not tasks:
        return "  (No tasks found matching criteria.)"
    
    if reference_date is None:
        reference_date = date.today()
    
    headers = ["ID", "Title", "Category", "Priority", "Due Date", "Status", "Recur", "Tags"]
    col_widths = [4, 28, 14, 8, 12, 10, 8, 16]
    
    # Table border lines
    separator = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_row = "| " + " | ".join(f"{h:<{w}}" for h, w in zip(headers, col_widths)) + " |"
    
    rows = [separator, header_row, separator]
    
    for t in tasks:
        t_id = str(t.get("id", ""))
        title = t.get("title", "")
        if len(title) > col_widths[1]:
            title = title[: col_widths[1] - 3] + "..."
        
        category = t.get("category", "")
        if len(category) > col_widths[2]:
            category = category[: col_widths[2] - 3] + "..."
            
        priority = t.get("priority", "")
        due = t.get("due", "") or "-"
        status = t.get("status", "")
        recur = t.get("recurrence", "none")
        tags_list = t.get("tags", [])
        tags_str = ", ".join(tags_list) if tags_list else "-"
        if len(tags_str) > col_widths[7]:
            tags_str = tags_str[: col_widths[7] - 3] + "..."
            
        # Determine coloring
        task_overdue = is_overdue(t.get("due", ""), status, reference_date)
        task_today = is_due_today(t.get("due", ""), status, reference_date)
        task_done = status == STATUS_DONE
        
        # Display cells
        cell_id = f"{t_id:>{col_widths[0]}}"
        cell_title = f"{title:<{col_widths[1]}}"
        cell_cat = f"{category:<{col_widths[2]}}"
        cell_pri = f"{priority:<{col_widths[3]}}"
        cell_due = f"{due:<{col_widths[4]}}"
        cell_status = f"{status:<{col_widths[5]}}"
        cell_recur = f"{recur:<{col_widths[6]}}"
        cell_tags = f"{tags_str:<{col_widths[7]}}"
        
        # Apply row or cell color highlight
        if task_done:
            cell_status = colorize(cell_status, COLOR_GREEN, config)
        elif task_overdue:
            cell_due = colorize(cell_due, COLOR_RED + COLOR_BOLD, config)
            cell_status = colorize(cell_status, COLOR_RED, config)
        elif task_today:
            cell_due = colorize(cell_due, COLOR_YELLOW + COLOR_BOLD, config)
            cell_status = colorize(cell_status, COLOR_YELLOW, config)
            
        if priority == PRIORITY_HIGH and not task_done:
            cell_pri = colorize(cell_pri, COLOR_MAGENTA + COLOR_BOLD, config)
            
        row_str = f"| {cell_id} | {cell_title} | {cell_cat} | {cell_pri} | {cell_due} | {cell_status} | {cell_recur} | {cell_tags} |"
        rows.append(row_str)
        
    rows.append(separator)
    return "\n".join(rows)


# ============================================================================
# USER INPUT HANDLING & SAFE VALIDATION
# ============================================================================

def prompt_input(prompt_msg: str, allow_empty: bool = True) -> str:
    """
    Prompt user for string input, handling Ctrl+C, Ctrl+D, and input loops cleanly.
    
    :param prompt_msg: Prompt text displayed to user.
    :param allow_empty: Whether an empty string is acceptable.
    :return: Stripped input string.
    """
    while True:
        try:
            val = input(prompt_msg).strip()
            if not val and not allow_empty:
                print("Error: Input cannot be empty. Please enter a valid value.")
                continue
            return val
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled by user.")
            return ""


def prompt_int(prompt_msg: str) -> Optional[int]:
    """
    Prompt for a positive integer (e.g. task ID). Returns None if user enters empty input.
    
    :param prompt_msg: Prompt text.
    :return: Integer or None if cancelled/empty.
    """
    while True:
        val = prompt_input(prompt_msg)
        if not val:
            return None
        try:
            parsed = int(val)
            if parsed <= 0:
                print("Error: Please enter a positive number.")
                continue
            return parsed
        except ValueError:
            print("Error: Invalid numeric input. Please enter digits only.")


def prompt_confirm(prompt_msg: str, default: bool = False) -> bool:
    """
    Prompt for yes/no confirmation.
    
    :param prompt_msg: Prompt message.
    :param default: Default result if user presses enter.
    :return: True if confirmed, False otherwise.
    """
    hint = " [Y/n]: " if default else " [y/N]: "
    response = prompt_input(prompt_msg + hint).lower()
    if not response:
        return default
    return response in ("y", "yes")


# ============================================================================
# CORE TASK OPERATIONS (ADD, UPDATE, DONE, DELETE, CLEAR)
# ============================================================================

def find_task_by_id(tasks: List[Dict[str, Any]], task_id: int) -> Optional[Dict[str, Any]]:
    """Find a task in the list matching task_id."""
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


def get_next_task_id(tasks: List[Dict[str, Any]]) -> int:
    """Calculate the next auto-increment integer ID."""
    if not tasks:
        return 1
    return max(t.get("id", 0) for t in tasks) + 1


def check_duplicate_title(tasks: List[Dict[str, Any]], title: str, exclude_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Check if a task with the same title already exists (case-insensitive).
    
    :param tasks: Tasks list.
    :param title: Task title to check.
    :param exclude_id: Optional ID to exclude (when updating).
    :return: Existing task dict if duplicate found, None otherwise.
    """
    clean_title = title.strip().lower()
    for t in tasks:
        if exclude_id is not None and t.get("id") == exclude_id:
            continue
        if t.get("title", "").strip().lower() == clean_title:
            return t
    return None


def action_add_task(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Interactive workflow to add a new task."""
    print("\n" + colorize("--- Add New Task ---", COLOR_CYAN + COLOR_BOLD, config))
    
    title = prompt_input("Task Title (required): ", allow_empty=False)
    if not title:
        return
    
    # Duplicate detection (Upgrade J)
    existing = check_duplicate_title(tasks, title)
    if existing:
        warning_msg = (
            f"Warning: A task with title '{existing.get('title')}' already exists "
            f"(ID: {existing.get('id')}, Status: {existing.get('status')})."
        )
        print(colorize(warning_msg, COLOR_YELLOW, config))
        if not prompt_confirm("Do you still want to add this task?", default=False):
            print("Add task cancelled.")
            return
            
    default_cat = config.get("default_category", "General")
    category = prompt_input(f"Category [Enter for '{default_cat}']: ") or default_cat
    
    default_pri = config.get("default_priority", PRIORITY_MEDIUM)
    print(f"Priority options: [H]igh, [M]edium, [L]ow (default: {default_pri})")
    pri_in = prompt_input("Priority [H/M/L]: ").upper()
    if pri_in == "H":
        priority = PRIORITY_HIGH
    elif pri_in == "L":
        priority = PRIORITY_LOW
    elif pri_in == "M":
        priority = PRIORITY_MEDIUM
    else:
        priority = default_pri
        
    # Due Date (Natural Date Parsing - Upgrade B)
    while True:
        due_in = prompt_input("Due Date (YYYY-MM-DD, 'today', 'tomorrow', '+3d', '+2w', 'fri', or Enter for none): ")
        if not due_in:
            due_date = ""
            break
        try:
            due_date = parse_date_input(due_in)
            break
        except ValueError as e:
            print(f"Error: {e}")
            
    notes = prompt_input("Notes (optional): ")
    
    tags_in = prompt_input("Tags (comma-separated, optional): ")
    tags = [tag.strip() for tag in tags_in.split(",") if tag.strip()] if tags_in else []
    
    print("Recurrence: [1] none, [2] daily, [3] weekly, [4] monthly")
    rec_choice = prompt_input("Recurrence [1-4, Enter for none]: ")
    rec_map = {"1": REC_NONE, "2": REC_DAILY, "3": REC_WEEKLY, "4": REC_MONTHLY}
    recurrence = rec_map.get(rec_choice, REC_NONE)
    
    # Record undo snapshot before mutation
    record_undo_state(f"Add task '{title}'", tasks)
    
    new_task: Dict[str, Any] = {
        "id": get_next_task_id(tasks),
        "title": title,
        "category": category,
        "priority": priority,
        "due": due_date,
        "status": STATUS_PENDING,
        "created": datetime.now().strftime(DATETIME_FORMAT),
        "completed": "",
        "notes": notes,
        "tags": tags,
        "recurrence": recurrence,
    }
    
    tasks.append(new_task)
    safe_save_tasks(tasks)
    print(colorize(f"Success: Task #{new_task['id']} '{title}' added successfully!", COLOR_GREEN, config))


def action_view_tasks(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Interactive workflow to view tasks with filters and sorting."""
    print("\n" + colorize("--- View Tasks ---", COLOR_CYAN + COLOR_BOLD, config))
    print("Filter by:")
    print("  1. All Tasks")
    print("  2. Pending Tasks")
    print("  3. Completed Tasks")
    print("  4. Overdue Tasks")
    print("  5. Due Today")
    print("  6. Due This Week")
    print("  7. By Category")
    print("  8. By Tag")
    
    filter_choice = prompt_input("Choose filter [1-8, Enter for All]: ")
    filter_map = {
        "1": "all",
        "2": "pending",
        "3": "completed",
        "4": "overdue",
        "5": "due_today",
        "6": "due_this_week",
        "7": "category",
        "8": "tag",
    }
    filter_type = filter_map.get(filter_choice, "all")
    filter_arg = None
    
    if filter_type == "category":
        # Show existing categories
        cats = sorted(list(set(t.get("category", "General") for t in tasks)))
        if cats:
            print("Existing categories: " + ", ".join(cats))
        filter_arg = prompt_input("Enter category name: ", allow_empty=False)
        if not filter_arg:
            return
    elif filter_type == "tag":
        filter_arg = prompt_input("Enter tag name: ", allow_empty=False)
        if not filter_arg:
            return
            
    filtered = filter_tasks(tasks, filter_type, filter_arg, config)
    
    # Sorting option (Upgrade E)
    print("\nSort by:")
    print("  1. Due Date")
    print("  2. Priority (High -> Low)")
    print("  3. Category (A-Z)")
    print("  4. Creation Date (Newest first)")
    print("  5. ID (Default)")
    sort_choice = prompt_input("Choose sort [1-5, Enter for ID]: ")
    sort_map = {
        "1": "due",
        "2": "priority",
        "3": "category",
        "4": "created",
        "5": "id",
    }
    sort_key = sort_map.get(sort_choice, "id")
    sorted_tasks = sort_tasks(filtered, sort_key)
    
    print(f"\nShowing {len(sorted_tasks)} task(s):")
    print(format_task_table(sorted_tasks, config))


def action_update_task(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Interactive workflow to update task details (Enter keeps current)."""
    print("\n" + colorize("--- Update Task ---", COLOR_CYAN + COLOR_BOLD, config))
    task_id = prompt_int("Enter Task ID to update: ")
    if task_id is None:
        return
        
    task = find_task_by_id(tasks, task_id)
    if not task:
        print(f"Error: Task #{task_id} not found.")
        return
        
    print(f"\nEditing Task #{task['id']}: '{task['title']}' (Press Enter to keep current value)")
    
    # Title
    new_title = prompt_input(f"Title [{task['title']}]: ")
    if new_title and new_title != task["title"]:
        dup = check_duplicate_title(tasks, new_title, exclude_id=task_id)
        if dup:
            print(colorize(f"Warning: A task with title '{new_title}' already exists (ID: {dup['id']}).", COLOR_YELLOW, config))
            if not prompt_confirm("Keep this title anyway?", default=False):
                new_title = task["title"]
    else:
        new_title = task["title"]
        
    # Category
    new_cat = prompt_input(f"Category [{task.get('category', 'General')}]: ") or task.get("category", "General")
    
    # Priority
    curr_pri = task.get("priority", PRIORITY_MEDIUM)
    pri_in = prompt_input(f"Priority (High/Medium/Low) [{curr_pri}]: ").upper()
    if pri_in in ("H", "HIGH"):
        new_pri = PRIORITY_HIGH
    elif pri_in in ("M", "MEDIUM"):
        new_pri = PRIORITY_MEDIUM
    elif pri_in in ("L", "LOW"):
        new_pri = PRIORITY_LOW
    else:
        new_pri = curr_pri
        
    # Due Date
    curr_due = task.get("due", "")
    due_prompt = f"Due Date (YYYY-MM-DD/today/+3d/clear) [{curr_due or 'none'}]: "
    while True:
        due_in = prompt_input(due_prompt)
        if not due_in:
            new_due = curr_due
            break
        if due_in.lower() == "clear":
            new_due = ""
            break
        try:
            new_due = parse_date_input(due_in)
            break
        except ValueError as e:
            print(f"Error: {e}")
            
    # Notes
    curr_notes = task.get("notes", "")
    new_notes = prompt_input(f"Notes [{curr_notes or 'none'}]: ")
    if not new_notes and new_notes != "":
        new_notes = curr_notes
    elif new_notes == "":
        new_notes = curr_notes  # Pressed enter to keep
        
    # Tags
    curr_tags_str = ", ".join(task.get("tags", []))
    new_tags_in = prompt_input(f"Tags [{curr_tags_str or 'none'}]: ")
    if new_tags_in:
        new_tags = [tag.strip() for tag in new_tags_in.split(",") if tag.strip()]
    else:
        new_tags = task.get("tags", [])
        
    # Recurrence
    curr_rec = task.get("recurrence", REC_NONE)
    print(f"Recurrence options: [1] none, [2] daily, [3] weekly, [4] monthly (current: {curr_rec})")
    rec_choice = prompt_input("Recurrence [1-4 or Enter to keep]: ")
    rec_map = {"1": REC_NONE, "2": REC_DAILY, "3": REC_WEEKLY, "4": REC_MONTHLY}
    new_rec = rec_map.get(rec_choice, curr_rec)
    
    # Save undo snapshot
    record_undo_state(f"Update task #{task_id}", tasks)
    
    # Update fields
    task["title"] = new_title
    task["category"] = new_cat
    task["priority"] = new_pri
    task["due"] = new_due
    task["notes"] = new_notes
    task["tags"] = new_tags
    task["recurrence"] = new_rec
    
    safe_save_tasks(tasks)
    print(colorize(f"Success: Task #{task_id} updated successfully!", COLOR_GREEN, config))


def action_mark_done(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """
    Mark a task as completed and automatically generate recurrence if configured.
    """
    print("\n" + colorize("--- Mark Task as Done ---", COLOR_CYAN + COLOR_BOLD, config))
    task_id = prompt_int("Enter Task ID to mark as done: ")
    if task_id is None:
        return
        
    task = find_task_by_id(tasks, task_id)
    if not task:
        print(f"Error: Task #{task_id} not found.")
        return
        
    if task.get("status") == STATUS_DONE:
        print(f"Task #{task_id} is already marked as Done.")
        return
        
    # Record undo snapshot
    record_undo_state(f"Mark done task #{task_id}", tasks)
    
    now_str = datetime.now().strftime(DATETIME_FORMAT)
    task["status"] = STATUS_DONE
    task["completed"] = now_str
    
    print(colorize(f"Success: Task #{task_id} '{task['title']}' marked as Done!", COLOR_GREEN, config))
    
    # Recurring Task Automation (Upgrade C)
    recurrence = task.get("recurrence", REC_NONE)
    if recurrence in (REC_DAILY, REC_WEEKLY, REC_MONTHLY):
        next_due = calculate_next_due_date(task.get("due", ""), recurrence)
        new_recurring_task: Dict[str, Any] = {
            "id": get_next_task_id(tasks),
            "title": task["title"],
            "category": task.get("category", "General"),
            "priority": task.get("priority", PRIORITY_MEDIUM),
            "due": next_due,
            "due_time": task.get("due_time", "") or "",
            "alert_offset": task.get("alert_offset", "at_due") or "at_due",
            "status": STATUS_PENDING,
            "created": now_str,
            "completed": "",
            "notes": task.get("notes", ""),
            "tags": list(task.get("tags", [])),
            "recurrence": recurrence,
        }
        tasks.append(new_recurring_task)
        recur_msg = (
            f"Recurring task created: #{new_recurring_task['id']} '{new_recurring_task['title']}' "
            f"due on {next_due} ({recurrence})."
        )
        print(colorize(recur_msg, COLOR_CYAN, config))
        
    safe_save_tasks(tasks)


def action_delete_task(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Interactive workflow to delete a task with confirmation."""
    print("\n" + colorize("--- Delete Task ---", COLOR_CYAN + COLOR_BOLD, config))
    task_id = prompt_int("Enter Task ID to delete: ")
    if task_id is None:
        return
        
    task = find_task_by_id(tasks, task_id)
    if not task:
        print(f"Error: Task #{task_id} not found.")
        return
        
    if not prompt_confirm(f"Are you sure you want to delete Task #{task_id} '{task['title']}'?", default=False):
        print("Deletion cancelled.")
        return
        
    record_undo_state(f"Delete task #{task_id} '{task['title']}'", tasks)
    tasks.remove(task)
    safe_save_tasks(tasks)
    print(colorize(f"Success: Task #{task_id} deleted successfully.", COLOR_GREEN, config))


def action_search_tasks(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Search tasks across titles, categories, notes, or tags."""
    print("\n" + colorize("--- Search Tasks ---", COLOR_CYAN + COLOR_BOLD, config))
    query = prompt_input("Enter search keyword: ", allow_empty=False)
    if not query:
        return
        
    results = search_tasks(tasks, query)
    print(f"\nFound {len(results)} match(es) for '{query}':")
    print(format_task_table(results, config))


def action_smart_suggestions(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Display top recommended tasks to work on next with explanation."""
    print("\n" + colorize("--- Intelligent Recommendations (What to do Next) ---", COLOR_CYAN + COLOR_BOLD, config))
    suggestions = get_smart_suggestions(tasks, top_n=5)
    
    if not suggestions:
        print("  No pending tasks to recommend! You are all caught up.")
        return
        
    print(f"{'Rank':<5} {'ID':<5} {'Title':<30} {'Score':<7} {'Reason'}")
    print("-" * 75)
    for rank, (task, score, reason) in enumerate(suggestions, 1):
        t_id = task.get("id", 0)
        title = task.get("title", "")
        if len(title) > 28:
            title = title[:25] + "..."
        score_colored = colorize(f"{score:>3} pts", COLOR_YELLOW + COLOR_BOLD, config)
        print(f"#{rank:<4} {t_id:<5} {title:<30} {score_colored}  {reason}")


def action_summary_report(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Display comprehensive productivity statistics."""
    summary = generate_summary_data(tasks)
    print("\n" + format_summary_report(summary))


def action_export_options(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Export tasks to report.txt or CSV file."""
    print("\n" + colorize("--- Export Data ---", COLOR_CYAN + COLOR_BOLD, config))
    print("  1. Export Summary Report to TXT (report.txt)")
    print("  2. Export All Tasks to CSV (tasks_export.csv)")
    print("  3. Export Both")
    
    choice = prompt_input("Choose export option [1-3]: ")
    if choice == "1":
        if export_report_to_txt(tasks, REPORT_FILE):
            print(colorize(f"Success: Report exported to '{REPORT_FILE}'", COLOR_GREEN, config))
        else:
            print("Error exporting report to text.")
    elif choice == "2":
        if export_tasks_to_csv(tasks, CSV_EXPORT_FILE):
            print(colorize(f"Success: Tasks exported to '{CSV_EXPORT_FILE}'", COLOR_GREEN, config))
        else:
            print("Error exporting tasks to CSV.")
    elif choice == "3":
        ok1 = export_report_to_txt(tasks, REPORT_FILE)
        ok2 = export_tasks_to_csv(tasks, CSV_EXPORT_FILE)
        if ok1 and ok2:
            print(colorize(f"Success: Exported to '{REPORT_FILE}' and '{CSV_EXPORT_FILE}'", COLOR_GREEN, config))
        else:
            print("Error exporting one or more files.")
    else:
        print("Invalid export option.")


def action_clear_completed(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> None:
    """Remove all completed tasks with user confirmation."""
    completed = [t for t in tasks if t.get("status") == STATUS_DONE]
    if not completed:
        print("\nNo completed tasks to clear.")
        return
        
    print("\n" + colorize("--- Clear Completed Tasks ---", COLOR_CYAN + COLOR_BOLD, config))
    if not prompt_confirm(f"Are you sure you want to clear all {len(completed)} completed task(s)?", default=False):
        print("Operation cancelled.")
        return
        
    record_undo_state(f"Clear {len(completed)} completed task(s)", tasks)
    tasks[:] = [t for t in tasks if t.get("status") != STATUS_DONE]
    safe_save_tasks(tasks)
    print(colorize(f"Success: Cleared {len(completed)} completed task(s).", COLOR_GREEN, config))


def action_undo(tasks: List[Dict[str, Any]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Undo the last mutating operation."""
    global undo_stack
    if not undo_stack:
        print("\nNotice: Nothing to undo.")
        return tasks
        
    restored, desc = perform_undo(tasks)
    if desc:
        safe_save_tasks(restored)
        print(colorize(f"\nUndo Successful: Reverted '{desc}'.", COLOR_GREEN, config))
        return restored
    return tasks


def action_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """Configure application settings."""
    print("\n" + colorize("--- Application Settings ---", COLOR_CYAN + COLOR_BOLD, config))
    print(f"  1. Default Priority    : {config.get('default_priority', PRIORITY_MEDIUM)}")
    print(f"  2. Default Category    : {config.get('default_category', 'General')}")
    print(f"  3. Week Start Day      : {config.get('week_start_day', 'Monday')}")
    print(f"  4. ANSI Color Output   : {'Enabled' if config.get('enable_color', True) else 'Disabled'}")
    print("  0. Back to Main Menu")
    
    choice = prompt_input("Select setting to modify [0-4]: ")
    if choice == "1":
        pri = prompt_input("Enter default priority (High/Medium/Low): ").capitalize()
        if pri in VALID_PRIORITIES:
            config["default_priority"] = pri
            save_config(config)
            print("Default priority updated.")
        else:
            print("Invalid priority.")
    elif choice == "2":
        cat = prompt_input("Enter default category: ", allow_empty=False)
        if cat:
            config["default_category"] = cat
            save_config(config)
            print("Default category updated.")
    elif choice == "3":
        day = prompt_input("Week start day (Monday/Sunday): ").capitalize()
        if day in ("Monday", "Sunday"):
            config["week_start_day"] = day
            save_config(config)
            print("Week start day updated.")
        else:
            print("Invalid day choice.")
    elif choice == "4":
        curr = config.get("enable_color", True)
        config["enable_color"] = not curr
        save_config(config)
        print(f"Color output is now {'Enabled' if config['enable_color'] else 'Disabled'}.")
        
    return config


# ============================================================================
# MAIN APPLICATION LOOP & CLI MENU
# ============================================================================

def display_main_menu(config: Dict[str, Any]) -> None:
    """Print the main navigation menu."""
    header = "=================== TASKTACT - INTELLIGENT TO-DO ==================="
    print("\n" + colorize(header, COLOR_CYAN + COLOR_BOLD, config))
    print("  1. Add Task")
    print("  2. View Tasks (with Filters & Sorting)")
    print("  3. Update Task")
    print("  4. Mark Task Done")
    print("  5. Delete Task")
    print("  6. Search Tasks")
    print("  7. Smart Suggestions (What to do next)")
    print("  8. Summary Report & Productivity Stats")
    print("  9. Export Data (TXT Report / CSV)")
    print(" 10. Clear Completed Tasks")
    print("  U. Undo Last Action")
    print("  S. Settings / Preferences")
    print("  0. Save and Exit")
    print("-" * 68)


def main() -> None:
    """Main program entry point."""
    init_terminal_colors()
    config = load_config()
    tasks = load_tasks(TASKS_FILE, BACKUP_FILE)
    
    welcome_banner = (
        "Welcome to TaskTact! Managing tasks intelligently.\n"
        f"Loaded {len(tasks)} task(s). Type menu number to begin."
    )
    print(colorize(welcome_banner, COLOR_GREEN, config))
    
    while True:
        try:
            display_main_menu(config)
            choice = prompt_input("Enter choice [0-10, U, S]: ").upper()
            
            if choice == "1":
                action_add_task(tasks, config)
            elif choice == "2":
                action_view_tasks(tasks, config)
            elif choice == "3":
                action_update_task(tasks, config)
            elif choice == "4":
                action_mark_done(tasks, config)
            elif choice == "5":
                action_delete_task(tasks, config)
            elif choice == "6":
                action_search_tasks(tasks, config)
            elif choice == "7":
                action_smart_suggestions(tasks, config)
            elif choice == "8":
                action_summary_report(tasks, config)
            elif choice == "9":
                action_export_options(tasks, config)
            elif choice == "10":
                action_clear_completed(tasks, config)
            elif choice == "U":
                tasks = action_undo(tasks, config)
            elif choice == "S":
                config = action_settings(config)
            elif choice == "0":
                safe_save_tasks(tasks)
                print(colorize("\nAll tasks saved successfully. Thank you for using TaskTact! Goodbye.", COLOR_GREEN, config))
                break
            else:
                print("Invalid menu choice. Please enter a valid number or option.")
                
        except (KeyboardInterrupt, EOFError):
            print("\n")
            if prompt_confirm("Do you want to save and exit?", default=True):
                safe_save_tasks(tasks)
                print("Saved. Goodbye!")
                break
        except Exception as err:
            logging.exception(f"Unexpected error in main loop: {err}")
            print(f"\nAn error occurred: {err}. Details have been logged to '{LOG_FILE}'.")


if __name__ == "__main__":
    main()
