# -*- coding: utf-8 -*-
"""
AgentTaskManager — foundation for running several assistant tasks at once.

Today the chat runs ONE request at a time (plus the composer's message
queue). This module is the parallel-execution substrate for the next step:
multiple agent tasks in flight, each on its own worker thread, with a
central registry the UI can render as task cards (running / done / failed)
and cancel individually.

Design constraints (why this is safe inside Revit):
- Worker threads NEVER touch the Revit API directly. Every tool call still
  goes through core.server._execute_tool, which marshals write tools onto
  Revit's main thread via ExternalEvent — that queue is the real
  serializer, so N parallel tasks contend there and nowhere else.
- Parallelism therefore pays off for the slow parts that are NOT Revit:
  LLM turns, HTTP, retrieval, report building. Two write-heavy tasks will
  interleave transactions and confuse Undo — callers must keep at most
  ONE write task running (use `writer` slots below).
- Cancellation is cooperative: task functions receive the AgentTask and
  should check `task.is_cancelled()` between steps (AgentLoop.cancel()
  plugs straight into this).

Intended wiring (script.py, later):
    mgr = get_task_manager()
    task = mgr.submit(u"Audit warnings", run_fn, writer=False,
                      on_done=lambda t: ui_update(t))
    ... mgr.list_tasks() -> task cards; mgr.cancel(task.id) from the card.

Pure Python (threading only), IronPython 2.7 + CPython 3 compatible.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Agent Task Manager"

import threading
import time
import traceback

# Task lifecycle states
PENDING   = "pending"
RUNNING   = "running"
DONE      = "done"
FAILED    = "failed"
CANCELLED = "cancelled"

# At most this many tasks run at once; extras wait in submission order.
MAX_PARALLEL_TASKS = 3


class AgentTask(object):
    """One tracked unit of background work."""

    def __init__(self, task_id, title, writer=False):
        self.id       = task_id
        self.title    = title
        self.writer   = writer          # True = may modify the model
        self.status   = PENDING
        self.result   = None            # return value of the task fn
        self.error    = None            # unicode traceback tail on failure
        self.created  = time.time()
        self.started  = None
        self.finished = None
        self._cancel  = threading.Event()

    def cancel(self):
        """Request a cooperative stop (the fn must check is_cancelled)."""
        self._cancel.set()

    def is_cancelled(self):
        return self._cancel.is_set()

    def is_active(self):
        return self.status in (PENDING, RUNNING)

    def duration(self):
        """Seconds spent running so far (0.0 before start)."""
        if self.started is None:
            return 0.0
        return (self.finished or time.time()) - self.started

    def snapshot(self):
        """Plain-dict view for UI rendering / logging."""
        return {
            "id": self.id, "title": self.title, "status": self.status,
            "writer": self.writer, "duration": round(self.duration(), 1),
            "error": self.error,
        }


class AgentTaskManager(object):
    """Thread-safe registry + bounded runner for AgentTasks.

    Guarantees:
    - at most MAX_PARALLEL_TASKS tasks RUNNING at any moment;
    - at most ONE task with writer=True RUNNING at any moment (model
      writes must never interleave transactions from two requests);
    - completed tasks stay listed until clear_finished() so the UI can
      show results.
    """

    def __init__(self, max_parallel=MAX_PARALLEL_TASKS):
        self._lock         = threading.Lock()
        self._tasks        = []      # ordered: submission order
        self._queue        = []      # AgentTasks waiting for a slot
        self._running      = 0
        self._writer_busy  = False
        self._max_parallel = max_parallel
        self._next_id      = 1

    # ── Public API ────────────────────────────────────────────────────────

    def submit(self, title, fn, writer=False, on_done=None):
        """Register + (slot permitting) start `fn(task)` on a worker thread.

        fn: callable taking the AgentTask (for is_cancelled checks); its
            return value lands in task.result.
        writer: True when the task may modify the model — serialized so
            only one writer runs at a time.
        on_done: optional callable(task), invoked on the WORKER thread
            after the task reaches a terminal state (marshal to the UI
            dispatcher yourself).
        Returns the AgentTask immediately.
        """
        with self._lock:
            task = AgentTask(self._next_id, u"{}".format(title or u"Task"),
                             writer=bool(writer))
            self._next_id += 1
            task._fn      = fn
            task._on_done = on_done
            self._tasks.append(task)
            self._queue.append(task)
        self._pump()
        return task

    def get(self, task_id):
        with self._lock:
            for t in self._tasks:
                if t.id == task_id:
                    return t
        return None

    def list_tasks(self, active_only=False):
        with self._lock:
            tasks = list(self._tasks)
        if active_only:
            tasks = [t for t in tasks if t.is_active()]
        return tasks

    def active_count(self):
        with self._lock:
            return self._running + len(self._queue)

    def cancel(self, task_id):
        """Cooperatively cancel one task. Pending tasks finish instantly."""
        task = self.get(task_id)
        if task is None:
            return False
        task.cancel()
        with self._lock:
            if task in self._queue:          # never started — finish now
                self._queue.remove(task)
                task.status   = CANCELLED
                task.finished = time.time()
                cb = getattr(task, "_on_done", None)
            else:
                cb = None                    # running: fn will notice
        if cb:
            try:
                cb(task)
            except Exception:
                pass
        return True


    def clear_finished(self):
        """Drop terminal tasks from the registry (UI 'clear' action)."""
        with self._lock:
            self._tasks = [t for t in self._tasks if t.is_active()]

    # ── Internals ─────────────────────────────────────────────────────────

    def _pump(self):
        """Start queued tasks while slots (and the writer slot) allow."""
        to_start = []
        with self._lock:
            i = 0
            while i < len(self._queue):
                if self._running >= self._max_parallel:
                    break
                task = self._queue[i]
                if task.writer and self._writer_busy:
                    i += 1               # writer slot taken — try next task
                    continue
                self._queue.pop(i)
                task.status  = RUNNING
                task.started = time.time()
                self._running += 1
                if task.writer:
                    self._writer_busy = True
                to_start.append(task)
        for task in to_start:
            th = threading.Thread(target=self._run, args=(task,))
            th.daemon = True
            th.start()

    def _run(self, task):
        try:
            if task.is_cancelled():
                task.status = CANCELLED
            else:
                task.result = task._fn(task)
                # status is the publication flag readers poll on — it must
                # be assigned LAST, after result/error are in place.
                task.status = CANCELLED if task.is_cancelled() else DONE
        except Exception:
            try:
                task.error = u"{}".format(traceback.format_exc()[-800:])
            except Exception:
                task.error = u"unknown error"
            task.status = FAILED
        finally:
            task.finished = time.time()
            with self._lock:
                self._running -= 1
                if task.writer:
                    self._writer_busy = False
            cb = getattr(task, "_on_done", None)
            if cb:
                try:
                    cb(task)
                except Exception:
                    pass
            self._pump()             # a slot freed — start waiting tasks


# ─── Parallel-eligibility policy ────────────────────────────────────────────────

def eligible_for_parallel(is_multi, node_count, has_writer, enabled):
    """Whether a graph plan should run its goals as concurrent task cards.

    Deliberately conservative — the same lesson as the reverted chunked
    BatchOut export: concurrency near Revit is only safe when it stays away
    from interleaved model writes.

    Parallelise ONLY when the caller opted in AND the plan is genuinely
    several independent READ goals:
      * enabled       — the agents.parallel_tasks kill switch is on;
      * is_multi      — the planner split the turn into >1 goal;
      * node_count>=2 — there is actually something to run side by side;
      * not has_writer — no goal modifies the model. A writer present means
        transactions could interleave, so the whole turn falls back to the
        existing one-at-a-time sequential path.

    Pure function of plain values so it is unit-testable without a real plan
    or any WPF/Revit context.
    """
    return bool(enabled) and bool(is_multi) and node_count >= 2 \
        and not has_writer


# ─── Process-wide singleton ────────────────────────────────────────────────────

_manager = None
_manager_lock = threading.Lock()


def get_task_manager():
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = AgentTaskManager()
    return _manager
