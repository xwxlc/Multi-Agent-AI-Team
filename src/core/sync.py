"""File sync layer — TASKS.md / STATUS.md / checkpoint / lock.

Provides human-readable persistence on top of in-memory TaskQueue + SharedMemory.
All writes are synchronous (no file lock needed in single-process asyncio).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional

TASKS_MD = "TASKS.md"
STATUS_MD = "STATUS.md"
LOCK_FILE = "TASKS.md.lock"
CHECKPOINT_DIR = ".checkpoint"


# ═══════════════════════════════════════════════════════════════════
#  TASKS.md
# ═══════════════════════════════════════════════════════════════════

SECTION_ORDER = ["## Todo", "## Doing", "## Done", "## Failed"]


class TasksMd:
    """Read / write TASKS.md — the human-readable task board."""

    # ── read ──────────────────────────────────────

    @staticmethod
    def parse(path: str = TASKS_MD) -> dict[str, list[dict]]:
        """Parse TASKS.md into sections: {"todo": [...], "doing": [...], "done": [...], "failed": [...]}"""
        result: dict[str, list[dict]] = {"todo": [], "doing": [], "done": [], "failed": []}
        if not os.path.isfile(path):
            return result

        with open(path) as f:
            content = f.read()

        current_section = None
        for line in content.split("\n"):
            line = line.rstrip()
            if line.startswith("## "):
                current_section = line.strip()[3:].lower()
                continue
            if not line.startswith("- [") or not line.startswith("- [ ]") and not line.startswith("- [x]"):
                continue

            is_done = line.startswith("- [x]")
            task = TasksMd._parse_task_line(line)
            if task:
                task["_done_mark"] = is_done
                sec = current_section or "todo"
                result.setdefault(sec, [])
                result[sec].append(task)

        return result

    @staticmethod
    def _parse_task_line(line: str) -> Optional[dict]:
        """Parse '- [ ] task001 | role:analyst | title:xxx' into dict.
        First token is always the bare task ID, rest are key:value pairs."""
        line = re.sub(r"^-\s*\[[x ]\]\s*", "", line).strip()
        parts = line.split("|")
        if not parts:
            return None
        task: dict[str, str] = {}
        # First part is the task ID (no key prefix)
        task["task"] = parts[0].strip()
        # Remaining parts are key:value
        for part in parts[1:]:
            part = part.strip()
            if ":" in part:
                key, _, val = part.partition(":")
                key = key.strip()
                val = val.strip()
                task[key] = val
        return task if task.get("task") else None

    @staticmethod
    def find_task(path: str, task_id: str) -> Optional[dict]:
        """Find a specific task by id anywhere in TASKS.md."""
        sections = TasksMd.parse(path)
        for sec_name, tasks in sections.items():
            for t in tasks:
                if t.get("task") == task_id:
                    t["_section"] = sec_name
                    return t
        return None

    @staticmethod
    def find_pending_by_role(path: str, role: str) -> list[dict]:
        """Find all pending (Todo) tasks for a specific role."""
        sections = TasksMd.parse(path)
        results = []
        for t in sections.get("todo", []):
            if t.get("role") == role:
                results.append(t)
        return results

    # ── internal ───────────────────────────────

    @staticmethod
    def _rebuild_file(path: str, sections: dict[str, list[dict]]) -> None:
        """Rebuild entire TASKS.md from sections dict."""
        lines = ["# TASKS.md — 任务中心", ""]
        for header in ["## Todo", "## Doing", "## Done", "## Failed"]:
            sec_key = header[3:].lower()
            lines.append(header)
            lines.append("")
            for t in sections.get(sec_key, []):
                is_done = t.pop("_done_mark", False) or header == "## Done"
                lines.append(TasksMd._format_task(t, done=is_done))
            lines.append("")
        content = "\n".join(lines)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    # ── write / init ─────────────────────────────

    @staticmethod
    def init(path: str = TASKS_MD, initial_task: Optional[dict] = None) -> str:
        """Create or append to TASKS.md.

        If file exists, append initial_task to Todo (preserving history).
        If not, create fresh file with initial_task in Todo.
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        if os.path.isfile(path) and initial_task:
            sections = TasksMd.parse(path)
            if initial_task.get("task"):
                # Check if this task already exists (same ID) — skip if so
                for sec_tasks in sections.values():
                    for t in sec_tasks:
                        if t.get("task") == initial_task["task"] and \
                           t.get("title") == initial_task.get("title"):
                            with open(path) as f:
                                return f.read()
                initial_task["_done_mark"] = False
                sections.setdefault("todo", []).append(initial_task)
                TasksMd._rebuild_file(path, sections)
                with open(path) as f:
                    return f.read()
            return TasksMd._read_all(path)

        content = TasksMd._build_fresh(initial_task)
        with open(path, "w") as f:
            f.write(content)
        return content

    @staticmethod
    def _build_fresh(initial_task: Optional[dict] = None) -> str:
        lines = [
            "# TASKS.md — 任务中心",
            "",
            "## Todo",
            "",
        ]
        if initial_task:
            lines.append(TasksMd._format_task(initial_task, done=False))
            lines.append("")
        lines += [
            "## Doing",
            "",
            "## Done",
            "",
            "## Failed",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _read_all(path: str) -> str:
        with open(path) as f:
            return f.read()

    @staticmethod
    def add_task(path: str, task: dict, section: str = "todo") -> None:
        """Append a task line to the specified section."""
        if not os.path.isfile(path):
            TasksMd.init(path)

        sections = TasksMd.parse(path)
        task["_done_mark"] = (section == "done")
        sections.setdefault(section, []).append(task)
        TasksMd._rebuild_file(path, sections)

    @staticmethod
    def move_task(path: str, task_id: str, from_section: str, to_section: str,
                  **extra: str) -> bool:
        """Move a task from one section to another (e.g. Todo → Doing)."""
        if not os.path.isfile(path):
            return False

        sections = TasksMd.parse(path)
        found = None
        for t in sections.get(from_section, []):
            if t.get("task") == task_id:
                found = t
                break

        if found is None:
            return False

        sections[from_section].remove(found)
        for k, v in extra.items():
            found[k] = v
        found["_done_mark"] = (to_section == "done")
        sections.setdefault(to_section, []).append(found)
        TasksMd._rebuild_file(path, sections)
        return True

    @staticmethod
    def sync_from_queue(path: str, tasks: dict, completed: set, failed: set,
                        task_ids_to_add: list) -> None:
        """Sync TaskQueue state into TASKS.md — add new tasks, mark completed/failed."""
        if not os.path.isfile(path):
            TasksMd.init(path)

        with open(path) as f:
            content = f.read()

        for task in task_ids_to_add:
            if isinstance(task, str):
                continue
            if not hasattr(task, 'id'):
                continue
            # Only add if not already present
            existing = TasksMd.find_task(path, task.id)
            if existing:
                continue

            task_dict = {
                "task": task.id,
                "role": getattr(task, 'assigned_to', 'developer'),
                "title": getattr(task, 'title', task.id),
            }
            if hasattr(task, 'priority'):
                task_dict["priority"] = task.priority.name
            if hasattr(task, 'dependencies') and task.dependencies:
                task_dict["depends_on"] = ",".join(task.dependencies)

            line = TasksMd._format_task(task_dict, done=False)
            content = TasksMd._insert_into_section(content, "## Todo", line)

        # Mark completed
        for tid in completed:
            for sec in ["Todo", "Doing", "Done"]:
                content = TasksMd._remove_from_section(content, f"## {sec}", tid)
            task_dict = {"task": tid, "role": "", "title": tid}
            line = TasksMd._format_task(task_dict, done=True)
            content = TasksMd._insert_into_section(content, "## Done", line)

        # Mark failed
        for tid in failed:
            for sec in ["Todo", "Doing"]:
                content = TasksMd._remove_from_section(content, f"## {sec}", tid)
            task_dict = {"task": tid, "role": "", "title": tid}
            line = TasksMd._format_task(task_dict, done=False)
            content = TasksMd._insert_into_section(content, "## Failed", line)

        with open(path, "w") as f:
            f.write(content)

    @staticmethod
    def rebuild_queue(path: str, queue) -> int:
        """Rebuild a TaskQueue from TASKS.md. Returns number of tasks restored."""
        from .task_queue import Task, Priority, TaskStatus

        sections = TasksMd.parse(path)
        count = 0
        for section, tasks in sections.items():
            for t in tasks:
                tid = t.get("task", "")
                if not tid:
                    continue
                role = t.get("role", "developer")
                title = t.get("title", tid)
                priority = {"HIGH": Priority.HIGH, "MEDIUM": Priority.MEDIUM,
                            "LOW": Priority.LOW}.get(t.get("priority", "MEDIUM"), Priority.MEDIUM)
                deps_str = t.get("depends_on", "")
                deps = [d.strip() for d in deps_str.split(",") if d.strip()] if deps_str else []

                status = TaskStatus.PENDING
                if section == "done":
                    status = TaskStatus.COMPLETED
                elif section == "failed":
                    status = TaskStatus.FAILED
                elif section == "doing":
                    status = TaskStatus.PENDING  # Reset doing back to pending

                task = Task(
                    id=tid,
                    title=title,
                    description=f"恢复任务: {title}",
                    assigned_to=role,
                    priority=priority,
                    status=status,
                    dependencies=deps,
                )
                queue.add(task)
                if status == TaskStatus.COMPLETED:
                    queue._completed.add(tid)
                elif status == TaskStatus.FAILED:
                    queue._failed.add(tid)
                count += 1
        return count

    @staticmethod
    def archive_done(path: str = TASKS_MD, history_path: str = "TASKS_HISTORY.md") -> None:
        """Copy newly completed tasks into a date-organized history file.

        Reads Done section from TASKS.md, appends unseen tasks to TASKS_HISTORY.md
        under today's date section. TASKS.md is not modified.
        """
        sections = TasksMd.parse(path)
        done_tasks = sections.get("done", [])
        if not done_tasks:
            return

        today = datetime.now().strftime("%Y-%m-%d")

        # Parse existing history
        history: dict[str, list[str]] = {}
        recorded_ids: set[str] = set()
        if os.path.isfile(history_path):
            with open(history_path) as f:
                current_date = None
                for line in f:
                    line = line.rstrip()
                    if line.startswith("## "):
                        current_date = line[3:].strip()
                    elif line.startswith("- [") and current_date:
                        history.setdefault(current_date, []).append(line)
                        m = re.search(r"\]\s+(\S+)", line)
                        if m:
                            recorded_ids.add(m.group(1))

        # Add new done tasks not yet in history
        new_entries: list[str] = []
        for t in done_tasks:
            tid = t.get("task", "")
            if tid and tid not in recorded_ids:
                new_entries.append(TasksMd._format_task(t, done=True))
                recorded_ids.add(tid)

        if not new_entries:
            return

        history.setdefault(today, []).extend(new_entries)

        # Rebuild history file — newest date first
        lines = ["# 任务历史", ""]
        for date in sorted(history.keys(), reverse=True):
            entries = history[date]
            if entries:
                lines.append(f"## {date}")
                lines.append("")
                lines.extend(entries)
                lines.append("")

        with open(history_path, "w") as f:
            f.write("\n".join(lines))

    # ── internal helpers ─────────────────────────

    @staticmethod
    def _format_task(task: dict, done: bool = False) -> str:
        mark = "[x]" if done else "[ ]"
        parts = [f"- {mark} {task.get('task', '?')}"]
        if task.get("role"):
            parts.append(f"role:{task['role']}")
        if task.get("title"):
            parts.append(f"title:{task['title']}")
        if task.get("priority"):
            parts.append(f"priority:{task['priority']}")
        if task.get("depends_on"):
            parts.append(f"depends_on:{task['depends_on']}")
        return " | ".join(parts)

    @staticmethod
    def _insert_into_section(content: str, section_header: str, line: str) -> str:
        """Insert a line under the specified section header."""
        # Find the section header and the next section header
        idx = content.find(section_header)
        if idx == -1:
            return content

        # Find the end of this section's block (next ## header)
        next_idx = content.find("\n## ", idx + len(section_header))
        if next_idx == -1:
            next_idx = len(content)

        # Find the last task line in this section, or insert after header
        section_body = content[idx + len(section_header):next_idx]
        # Find last line that looks like a task or blank line after header
        lines = section_body.split("\n")
        insert_after = idx + len(section_header)
        for i, line_check in enumerate(lines):
            if line_check.startswith("- [") or line_check.startswith("- ["):
                insert_after = idx + len(section_header) + sum(len(l) + 1 for l in lines[:i + 1])

        before = content[:insert_after]
        after = content[insert_after:]
        return before + "\n" + line + after

    @staticmethod
    def _remove_from_section(content: str, section_header: str, task_id: str) -> str:
        """Remove a task line from a section by task_id."""
        idx = content.find(section_header)
        if idx == -1:
            return content

        next_idx = content.find("\n## ", idx + len(section_header))
        if next_idx == -1:
            next_idx = len(content)

        section_body = content[idx + len(section_header):next_idx]
        # Remove the line containing this task_id
        new_body_lines = []
        for line in section_body.split("\n"):
            if f" {task_id} " in line or line.strip().startswith(f"- [ ] {task_id}") or \
               line.strip().startswith(f"- [x] {task_id}"):
                continue
            new_body_lines.append(line)

        new_body = "\n".join(new_body_lines)
        return content[:idx + len(section_header)] + new_body + content[next_idx:]


# ═══════════════════════════════════════════════════════════════════
#  STATUS.md
# ═══════════════════════════════════════════════════════════════════

class StatusMd:
    """Write STATUS.md — live runtime status snapshot."""

    @staticmethod
    def write(path: str = STATUS_MD, current_agent: str = "",
              current_task: str = "", progress: str = "",
              elapsed: str = "", tasks_summary: str = "") -> str:
        lines = [
            "# 执行状态",
            "",
            f"- 当前 Agent: {current_agent or '（等待中）'}",
            f"- 当前任务: {current_task or '（无）'}",
            f"- 进度: {progress or '（无）'}",
            f"- 耗时: {elapsed or '（无）'}",
            f"- 任务: {tasks_summary or '（无）'}",
            f"- 上次更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        content = "\n".join(lines)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return content


# ═══════════════════════════════════════════════════════════════════
#  Checkpoint
# ═══════════════════════════════════════════════════════════════════

class Checkpoint:
    """Checkpoint persistence for long-running tasks (esp. developer)."""

    @staticmethod
    def _get_path(agent: str, task_id: str) -> str:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        return os.path.join(CHECKPOINT_DIR, f"{agent}_{task_id}.json")

    @staticmethod
    def save(agent: str, task_id: str, data: dict) -> str:
        """Save checkpoint data. Returns the file path."""
        payload = {
            "agent": agent,
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            **data,
        }
        path = Checkpoint._get_path(agent, task_id)
        with open(path, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def load(agent: str, task_id: str) -> Optional[dict]:
        """Load checkpoint data, or None if not found."""
        path = Checkpoint._get_path(agent, task_id)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def clear(agent: str, task_id: str) -> None:
        """Remove checkpoint file."""
        path = Checkpoint._get_path(agent, task_id)
        if os.path.isfile(path):
            os.remove(path)

    @staticmethod
    def list_all() -> list[dict]:
        """List all checkpoint files."""
        if not os.path.isdir(CHECKPOINT_DIR):
            return []
        results = []
        for fn in os.listdir(CHECKPOINT_DIR):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(CHECKPOINT_DIR, fn)) as f:
                        results.append(json.load(f))
                except Exception:
                    pass
        return results


# ═══════════════════════════════════════════════════════════════════
#  Lock
# ═══════════════════════════════════════════════════════════════════

class Lock:
    """Simple file-based lock to prevent concurrent runs."""

    @staticmethod
    def acquire(path: str = LOCK_FILE) -> bool:
        """Try to acquire lock. Returns True on success, False if already locked."""
        if os.path.isfile(path):
            return False
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return True

    @staticmethod
    def release(path: str = LOCK_FILE) -> None:
        """Release the lock."""
        if os.path.isfile(path):
            os.remove(path)

    @staticmethod
    def is_locked(path: str = LOCK_FILE) -> bool:
        return os.path.isfile(path)
