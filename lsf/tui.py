"""
lsf.tui -- full-screen Textual interface.

Layout:
    +---------------------------+--------------------------------+
    | Today's plan (timetable)  | Task table                     |
    |                           |                                |
    +---------------------------+--------------------------------+
    | Timer                     | Selected task details          |
    +---------------------------+--------------------------------+
    | Stats bar (remaining / pace / overload warnings)           |
    | Footer (keybindings)                                       |
    +------------------------------------------------------------+

All scheduling logic lives in task.py / scheduler.py -- this module only
renders state and forwards edits back through the same persistence helpers
the CLI commands use, so `lsf start` / `lsf done` etc. stay interoperable.
"""

from datetime import datetime, timedelta

from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Static

from .task import (
    Task, schedule_sliced, today_slices, edf_max_subset,
    _windows_for_day, _day_total_h, daylight_hours_until,
    BREAK_H, SLICE_H,
)
from .scheduler import (
    load_tasks, save_tasks, load_session, save_session, clear_session,
    import_csv, dict_to_task, export_ics, new_task_dict,
    archive_task, pop_history,
    PRIORITY_LABELS, DIFFICULTY_LABELS, DIFF_ICON, DEFAULT_ICS_PATH,
)
from .util import fmt_hours, fmt_dt, parse_due_date, parse_duration


# -- Markup helpers -----------------------------------------------------------

def _slack_markup(slack: float) -> str:
    color = "red" if slack < 0 else ("yellow" if slack < 2 else "green")
    sign  = "+" if slack >= 0 else ""
    return f"[{color}]{sign}{fmt_hours(slack)}[/]"


def _slack_bar_markup(slack: float, adjusted_estimate: float, width: int = 20) -> str:
    if slack <= 0:
        return f"[red]{'#' * width}[/]"
    cap    = max(adjusted_estimate * 2, 0.01)
    ratio  = min(slack / cap, 1.0)
    filled = int(ratio * width)
    color  = "green" if ratio > 0.5 else "yellow"
    return f"[{color}]{'|' * filled}[/][dim]{'.' * (width - filled)}[/]"


def _status_markup(t: Task, collectively_overloaded: bool) -> str:
    if t.overloaded:
        return "[red]impossible[/]"
    if t.will_be_late and collectively_overloaded:
        return "[dim]delayed by workload[/]"
    if t.will_be_late:
        return "[yellow]will be late[/]"
    return "[green]on track[/]"


def _session_elapsed_h(session: dict, now: datetime) -> float:
    started  = datetime.fromisoformat(session["started_at"])
    paused_h = session.get("paused_h", 0.0)
    if session.get("paused_at"):
        paused_h += (now - datetime.fromisoformat(session["paused_at"])
                     ).total_seconds() / 3600
    return max((now - started).total_seconds() / 3600 - paused_h, 0.0)


# -- Modal screens --------------------------------------------------------------

class TaskForm(ModalScreen):
    """Add/edit form. Dismisses with a task-field dict, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, task: dict | None = None):
        super().__init__()
        self._task_dict = task

    def compose(self) -> ComposeResult:
        t = self._task_dict
        title = "Edit task" if t else "New task"
        with Vertical(id="form-box"):
            yield Label(f"[bold]{title}[/]", id="form-title")
            yield Label("Name")
            yield Input(value=t["name"] if t else "", id="f-name")
            yield Label("Due  [dim](e.g. 25/03 23:59, friday 18:00, +3d, tonight)[/]")
            yield Input(
                value=fmt_dt(datetime.fromisoformat(t["due"])) if t else "",
                placeholder="tomorrow 23:59", id="f-due")
            yield Label("Estimate  [dim](e.g. 2h, 90m, 1h30m)[/]")
            yield Input(value=fmt_hours(t["raw_estimate"]) if t else "",
                        placeholder="1h", id="f-est")
            yield Label("Priority  [dim]1 low · 2 medium · 3 high · 4 critical[/]")
            yield Input(value=str(t["priority"]) if t else "2", id="f-pri")
            yield Label("Difficulty  [dim]1 light · 2 medium · 3 deep[/]")
            yield Input(value=str(t.get("difficulty", 2)) if t else "2", id="f-diff")
            yield Label("", id="form-error")
            with Horizontal(id="form-buttons"):
                yield Button("Save", variant="success", id="f-save")
                yield Button("Cancel", id="f-cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#f-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    @on(Button.Pressed, "#f-save")
    def _save(self) -> None:
        err = self.query_one("#form-error", Label)

        name = self.query_one("#f-name", Input).value.strip()
        if not name:
            err.update("[red]Name cannot be empty.[/]")
            return

        raw_due = self.query_one("#f-due", Input).value.strip()
        try:
            if self._task_dict and raw_due == fmt_dt(datetime.fromisoformat(self._task_dict["due"])):
                due = datetime.fromisoformat(self._task_dict["due"])
            else:
                due = parse_due_date(raw_due)
        except ValueError as e:
            err.update(f"[red]{escape(str(e))}[/]")
            return

        raw_est = self.query_one("#f-est", Input).value.strip()
        try:
            if self._task_dict and raw_est == fmt_hours(self._task_dict["raw_estimate"]):
                estimate = self._task_dict["raw_estimate"]
            else:
                estimate = parse_duration(raw_est)
        except ValueError:
            err.update("[red]Could not parse estimate (try 2h, 90m, 1h30m).[/]")
            return

        try:
            priority = int(self.query_one("#f-pri", Input).value.strip())
            if priority not in PRIORITY_LABELS:
                raise ValueError
        except ValueError:
            err.update("[red]Priority must be 1-4.[/]")
            return

        try:
            difficulty = int(self.query_one("#f-diff", Input).value.strip())
            if difficulty not in DIFFICULTY_LABELS:
                raise ValueError
        except ValueError:
            err.update("[red]Difficulty must be 1-3.[/]")
            return

        self.dismiss({
            "name":         name,
            "due":          due.isoformat(),
            "raw_estimate": estimate,
            "priority":     priority,
            "difficulty":   difficulty,
        })


class Confirm(ModalScreen):
    """Yes/No confirmation. Dismisses with True/False."""

    BINDINGS = [
        Binding("escape,n", "no", "No"),
        Binding("y", "yes", "Yes"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self._msg_text = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._msg_text, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="error", id="c-yes")
                yield Button("No", variant="primary", id="c-no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "c-yes")


class PanicScreen(ModalScreen):
    """Survival triage report -- same EDF logic as `lsf panic`."""

    BINDINGS = [Binding("escape,q,P", "dismiss_panic", "Close")]

    def __init__(self, report: str):
        super().__init__()
        self._report_text = report

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="panic-box"):
            yield Static(self._report_text)

    def action_dismiss_panic(self) -> None:
        self.dismiss()


def build_panic_report(tasks: list[Task], now: datetime) -> str:
    """Render the panic triage as Rich markup (mirror of scheduler.panic)."""
    def fresh_slack(t: Task) -> float:
        return daylight_hours_until(t.due, now) - t.remaining_estimate

    impossible = [t for t in tasks if fresh_slack(t) < 0]
    pos_slack  = [t for t in tasks if fresh_slack(t) >= 0]
    on_time, deferred = edf_max_subset(pos_slack, now)

    total_remaining = sum(t.remaining_estimate for t in pos_slack)
    latest_due      = max((t.due for t in pos_slack), default=now)
    available_total = daylight_hours_until(latest_due, now)

    lines = [f"[bold]PANIC MODE[/]  [dim](as of {now:%H:%M, %a %d %b})[/]", ""]

    if impossible:
        lines.append(f"[bold red]Write-offs ({len(impossible)}) -- "
                     f"missed regardless of order:[/]")
        for t in impossible:
            lines.append(f"  [red]x[/] {escape(t.name)}  "
                         f"[dim]due {fmt_dt(t.due)}, "
                         f"short by {fmt_hours(-fresh_slack(t))}[/]")
        lines.append("")

    shortfall = total_remaining - available_total
    if shortfall > 0:
        lines.append(f"[yellow]Workload exceeds available time "
                     f"by {fmt_hours(shortfall)}.[/]")
        lines.append(f"[dim]EDF found the maximum set that fits -- "
                     f"{len(on_time)} on time, {len(deferred)} deferred.[/]")
        lines.append("")

    if on_time:
        lines.append(f"[bold green]Achievable on time ({len(on_time)}) -- "
                     f"focus on these:[/]")
        for t in sorted(on_time, key=lambda t: t.due):
            stars = "*" * t.priority
            lines.append(f"  [green]+[/] [bold]{escape(t.name)}[/]  "
                         f"[dim]{DIFF_ICON.get(t.difficulty, '~')} {stars}  "
                         f"due {fmt_dt(t.due)}  "
                         f"est {fmt_hours(t.remaining_estimate)}[/]")
        lines.append("")

    if deferred:
        lines.append(f"[bold yellow]Defer or drop ({len(deferred)}):[/]")
        for t in sorted(deferred, key=lambda t: t.due):
            stars = "*" * t.priority
            lines.append(f"  [yellow]~[/] {escape(t.name)}  "
                         f"[dim]{DIFF_ICON.get(t.difficulty, '~')} {stars}  "
                         f"due {fmt_dt(t.due)}  "
                         f"est {fmt_hours(t.remaining_estimate)}[/]")
        lines.append("")

    if on_time:
        lines.append("[bold]Optimal session plan (on-time tasks only):[/]")
        opt_slices, _ = schedule_sliced(on_time, now, break_h=0.0)
        by_day: dict = {}
        for s in opt_slices:
            by_day.setdefault(s.start.date(), []).append(s)
        for d, day in list(by_day.items())[:7]:
            lines.append(f"  [dim]-- {d.strftime('%a %d %b')} --[/]")
            for s in day:
                lines.append(f"    {s.start:%H:%M} -> {s.end:%H:%M}  "
                             f"[bold]{escape(s.task.name)}[/]  "
                             f"[dim]{round(s.duration_h * 60)}m[/]")
        if len(by_day) > 7:
            lines.append(f"  [dim]... +{len(by_day) - 7} more day(s)[/]")
        lines.append("")

    lines.append("[dim]Press Esc to close.[/]")
    return "\n".join(lines)


# -- Main app -------------------------------------------------------------------

class LsfApp(App):
    """Least Slack First -- full-screen scheduler."""

    TITLE = "lsf -- Least Slack First"

    CSS = """
    #main-grid {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: 1fr auto;
        height: 1fr;
    }
    #today-panel, #task-panel, #timer-panel, #detail-panel {
        border: round $primary;
        padding: 0 1;
    }
    #today-panel  { row-span: 1; }
    #task-panel   { height: 100%; }
    #timer-panel  { height: auto; min-height: 5; }
    #detail-panel { height: auto; min-height: 5; }
    #stats-bar {
        height: auto;
        padding: 0 2;
        background: $surface;
    }
    #task-table { height: 1fr; }

    TaskForm, Confirm, PanicScreen { align: center middle; }
    #form-box {
        width: 60; height: auto; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    #form-box Input { margin-bottom: 1; }
    #form-buttons Button { margin-right: 2; }
    #confirm-box {
        width: 50; height: auto; padding: 1 2;
        border: thick $error; background: $surface;
    }
    #confirm-msg { margin-bottom: 1; }
    #confirm-buttons Button { margin-right: 2; }
    #panic-box {
        width: 80%; height: 80%; padding: 1 2;
        border: thick $error; background: $surface;
    }
    """

    BINDINGS = [
        Binding("a", "add_task", "Add"),
        Binding("e", "edit_task", "Edit"),
        Binding("d", "complete_task", "Done"),
        Binding("u", "undo", "Undo"),
        Binding("s", "toggle_timer", "Start/stop"),
        Binding("p", "toggle_pause", "Pause/resume"),
        Binding("x", "export_ics", "Export .ics"),
        Binding("P", "panic", "Panic", key_display="shift+p"),
        Binding("r", "reload", "Reload"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.raw:        list[dict]  = []
        self.tasks:      list[Task]  = []
        self.slices:     list        = []
        self.view_tasks: list[Task]  = []   # tasks in table display order
        self.collectively_overloaded = False
        self.any_impossible          = False
        self.edf_deferred: list[str] = []   # task names EDF cannot fit
        # Session alert state (break reminders)
        self._alert_session_key: str | None = None
        self._alert_k                        = 0
        self._pause_alert_done:  str | None = None

    # -- Layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Grid(id="main-grid"):
            yield Static(id="today-panel")
            with Vertical(id="task-panel"):
                yield Label("[bold]Tasks[/]")
                yield DataTable(id="task-table", cursor_type="row")
            yield Static(id="timer-panel")
            yield Static(id="detail-panel")
        yield Static(id="stats-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#task-table", DataTable)
        table.add_columns("#", "Task", "D", "Pri", "Due", "Left", "Slack", "Status")
        self.reload_from_disk()
        self.set_interval(1.0, self.refresh_timer)
        self.set_interval(60.0, self.refresh_schedule)

    # -- Data ------------------------------------------------------------------

    def reload_from_disk(self) -> None:
        self.raw = load_tasks()
        self.raw, added = import_csv(self.raw)
        if added:
            save_tasks(self.raw)
            self.notify(f"Auto-imported {added} task(s) from import.csv")
        self.refresh_schedule()

    def refresh_schedule(self) -> None:
        now = datetime.now()
        self.tasks = [dict_to_task(d) for d in self.raw]
        if self.tasks:
            self.slices, _ = schedule_sliced(self.tasks, now)
        else:
            self.slices = []

        total_remaining = sum(t.remaining_estimate for t in self.tasks)
        if self.tasks:
            latest_due      = max(t.due for t in self.tasks)
            available_total = daylight_hours_until(latest_due, now)
        else:
            available_total = 0.0
        self.any_impossible = any(t.overloaded for t in self.tasks)
        self.collectively_overloaded = (
            not self.any_impossible and total_remaining > available_total
        )
        # EDF feasibility lookahead: warn when per-deadline packing fails
        # even though no aggregate overload is visible yet
        if self.tasks:
            _, deferred = edf_max_subset(list(self.tasks), now)
            self.edf_deferred = [t.name for t in deferred]
        else:
            self.edf_deferred = []

        self.render_today(now)
        self.render_table(now)
        self.render_stats(now, total_remaining, available_total)
        self.refresh_timer()
        self.render_detail()

    def persist(self) -> None:
        save_tasks(self.raw)
        self.refresh_schedule()

    def selected_index(self) -> int | None:
        table = self.query_one("#task-table", DataTable)
        if not self.view_tasks or table.cursor_row is None:
            return None
        if not (0 <= table.cursor_row < len(self.view_tasks)):
            return None
        return table.cursor_row

    def selected_task(self) -> Task | None:
        idx = self.selected_index()
        return self.view_tasks[idx] if idx is not None else None

    def raw_for(self, task: Task) -> dict | None:
        return next((d for d in self.raw if d["id"] == task.id), None)

    # -- Rendering ---------------------------------------------------------------

    def render_today(self, now: datetime) -> None:
        panel = self.query_one("#today-panel", Static)
        day_slices, day_start, _day_end = today_slices(self.slices, now)

        lines: list[str] = []
        if day_start.date() == now.date():
            lines.append(f"[bold]Today's plan[/]  "
                         f"[dim]({day_start.date():%a %d %b})[/]")
        else:
            lines.append(f"[bold]Next working day[/]  "
                         f"[dim]({day_start.date():%a %d %b})[/]")
        lines.append("")

        if not day_slices:
            lines.append("[dim]No work scheduled for this period.[/]")
        else:
            session = load_session()
            for ws_t, we_t in _windows_for_day(day_start.date()):
                ws = datetime.combine(day_start.date(), ws_t)
                we = datetime.combine(day_start.date(), we_t)
                if we <= now:
                    continue
                win_min = round((we - ws).total_seconds() / 60)
                dur_str = (f"{win_min // 60}h{win_min % 60:02d}m"
                           if win_min >= 60 else f"{win_min}m")
                lines.append(f"[dim]-- {ws:%H:%M}-{we:%H:%M} ({dur_str}) --[/]")

                win_slices = [s for s in day_slices
                              if s.start < we and s.end > ws]
                if not win_slices:
                    lines.append("  [dim](free)[/]")
                prev_end = None
                for s in win_slices:
                    start = max(s.start, ws, now)
                    end   = min(s.end, we)
                    dur   = round((end - start).total_seconds() / 60)
                    if dur <= 0:
                        continue
                    if prev_end is not None and BREAK_H > 0:
                        gap = round((start - prev_end).total_seconds() / 60)
                        if 0 < gap <= round(BREAK_H * 60) + 1:
                            lines.append(f"  [dim]{prev_end:%H:%M} -> "
                                         f"{start:%H:%M}  (break {gap}m)[/]")
                    icon = DIFF_ICON.get(s.task.difficulty, "~")
                    late = "  [yellow]![/]" if s.will_be_late else ""
                    live = ""
                    if (session and session.get("task_id") == s.task.id
                            and s.start <= now <= s.end):
                        live = "  [cyan]>[/]"
                    lines.append(f"  {start:%H:%M} -> {end:%H:%M}  "
                                 f"[bold]{escape(s.task.name)}[/]  "
                                 f"[dim]{icon} {dur}m[/]{late}{live}")
                    prev_end = end
                lines.append("")

        # Days beyond today, condensed
        future = [s for s in self.slices if s not in day_slices]
        if future:
            per_task: dict[str, float] = {}
            for s in future:
                per_task[s.task.name] = per_task.get(s.task.name, 0) + s.duration_h
            lines.append("[dim]Later:[/]")
            for name, h in per_task.items():
                lines.append(f"  [dim]{escape(name)}  ({fmt_hours(h)})[/]")

        panel.update("\n".join(lines))

    def render_table(self, now: datetime) -> None:
        table = self.query_one("#task-table", DataTable)
        prev_row = table.cursor_row
        table.clear()

        self.view_tasks = sorted(self.tasks, key=lambda t: (t.due, -t.urgency))
        for i, t in enumerate(self.view_tasks, 1):
            due_label = ("today" if t.due.date() == now.date() else
                         "tmrw" if t.due.date() == (now + timedelta(days=1)).date()
                         else t.due.strftime("%a %d"))
            table.add_row(
                str(i),
                escape(t.name),
                DIFF_ICON.get(t.difficulty, "~"),
                "*" * t.priority,
                f"{due_label} {t.due:%H:%M}",
                fmt_hours(t.remaining_estimate),
                _slack_markup(t.slack),
                _status_markup(t, self.collectively_overloaded),
                key=t.id,
            )
        if self.view_tasks and prev_row is not None:
            table.move_cursor(row=min(prev_row, len(self.view_tasks) - 1))

    def render_detail(self) -> None:
        panel = self.query_one("#detail-panel", Static)
        t = self.selected_task()
        if t is None:
            panel.update("[dim]No task selected. Press [bold]a[/bold] to add one.[/]")
            return
        spent = (f"  [dim]({fmt_hours(t.time_spent)} spent)[/]"
                 if t.time_spent > 0 else "")
        panel.update(
            f"[bold]{escape(t.name)}[/]  [dim][{t.id}][/]\n"
            f"Due {fmt_dt(t.due)}  ·  "
            f"est {fmt_hours(t.raw_estimate)} -> "
            f"{fmt_hours(t.adjusted_estimate)} adjusted{spent}\n"
            f"Remaining [bold]{fmt_hours(t.remaining_estimate)}[/]  ·  "
            f"slack {_slack_markup(t.slack)}  "
            f"{_slack_bar_markup(t.slack, t.adjusted_estimate)}\n"
            f"Finish ~{fmt_dt(t.finish_time)}  ·  "
            f"{_status_markup(t, self.collectively_overloaded)}"
        )

    def render_stats(self, now: datetime, total_remaining: float,
                     available_total: float) -> None:
        bar = self.query_one("#stats-bar", Static)
        if not self.tasks:
            bar.update("[dim]No tasks.[/]")
            return

        day_h        = _day_total_h(now.date())
        working_days = available_total / day_h if day_h > 0 else 0.0
        parts = [f"[bold]{len(self.tasks)}[/] task(s)",
                 f"remaining [bold]{fmt_hours(total_remaining)}[/]"]

        if working_days < 1.0:
            color = ("red" if total_remaining > available_total else
                     "yellow" if total_remaining > available_total * 0.8
                     else "green")
            parts.append(f"needed today [{color}]{fmt_hours(total_remaining)}[/] "
                         f"[dim]of {fmt_hours(available_total)} available[/]")
        else:
            pace  = total_remaining / max(working_days, 1 / 24)
            color = ("red" if pace > day_h * 0.9 else
                     "yellow" if pace > day_h * 0.6 else "green")
            parts.append(f"pace [{color}]{fmt_hours(pace)}/day[/] "
                         f"[dim]over {working_days:.1f} working days[/]")

        line = "  ·  ".join(parts)
        if self.any_impossible:
            line += ("\n[bold red]OVERLOAD[/] some deadlines are impossible "
                     "-- press [bold]shift+p[/] for triage")
        elif self.collectively_overloaded:
            shortfall = total_remaining - available_total
            line += (f"\n[bold yellow]COLLECTIVE OVERLOAD[/] workload exceeds "
                     f"available time by {fmt_hours(shortfall)} "
                     f"-- press [bold]shift+p[/] for triage")
        elif self.edf_deferred:
            names = ", ".join(escape(n) for n in self.edf_deferred[:3])
            if len(self.edf_deferred) > 3:
                names += f" +{len(self.edf_deferred) - 3} more"
            line += (f"\n[bold yellow]DEADLINE RISK[/] cannot fit even "
                     f"optimally: {names} "
                     f"-- press [bold]shift+p[/] for triage")
        bar.update(line)

    def _check_session_alerts(self, session: dict | None, now: datetime) -> None:
        """Pomodoro-style reminders: break due after a full slice of work,
        and back-to-work once a pause exceeds the configured break length."""
        if not session:
            self._alert_session_key = None
            self._pause_alert_done  = None
            return

        t       = next((x for x in self.tasks
                        if x.id == session["task_id"]), None)
        name    = t.name if t else "current task"
        slice_h = SLICE_H.get(t.difficulty if t else 2, 1.0)

        if session.get("paused_at"):
            pkey = session["paused_at"]
            paused_for = (now - datetime.fromisoformat(pkey)
                          ).total_seconds() / 3600
            if (BREAK_H > 0 and paused_for >= BREAK_H
                    and self._pause_alert_done != pkey):
                self._pause_alert_done = pkey
                self.bell()
                self.notify(f"Break over -- press p to resume {name}.",
                            title="lsf", timeout=10)
            return

        self._pause_alert_done = None
        if slice_h <= 0:
            return
        k = int(_session_elapsed_h(session, now) // slice_h)
        if session["started_at"] != self._alert_session_key:
            # First sight of this session -- baseline, don't fire for the past
            self._alert_session_key = session["started_at"]
            self._alert_k           = k
        elif k > self._alert_k:
            self._alert_k = k
            self.bell()
            self.notify(f"{fmt_hours(k * slice_h)} on {name} -- "
                        f"time for a break (press p).",
                        title="lsf", timeout=10)

    def refresh_timer(self) -> None:
        panel = self.query_one("#timer-panel", Static)
        session = load_session()
        now  = datetime.now()
        self._check_session_alerts(session, now)
        if not session:
            panel.update("[bold]Timer[/]\n[dim]No active session. "
                         "Select a task and press [bold]s[/bold] to start.[/]")
            return
        t    = next((x for x in self.tasks if x.id == session["task_id"]), None)
        name = escape(t.name) if t else session["task_id"]
        h    = _session_elapsed_h(session, now)
        total_s = int(h * 3600)
        clock   = f"{total_s // 3600:02d}:{total_s % 3600 // 60:02d}:{total_s % 60:02d}"
        if session.get("paused_at"):
            panel.update(f"[bold]Timer[/]\n[yellow]|| {name}  {clock}  "
                         f"(paused)[/]\n[dim]p to resume[/]")
        else:
            panel.update(f"[bold]Timer[/]\n[green]> {name}  {clock}[/]\n"
                         f"[dim]s to stop & log · p to pause[/]")

    # -- Events ------------------------------------------------------------------

    @on(DataTable.RowHighlighted)
    def _row_changed(self) -> None:
        self.render_detail()

    # -- Actions -----------------------------------------------------------------

    def action_add_task(self) -> None:
        def done(fields: dict | None) -> None:
            if fields:
                self.raw.append(new_task_dict(**fields))
                self.persist()
                self.notify(f"Added: {fields['name']}")
        self.push_screen(TaskForm(), done)

    def action_edit_task(self) -> None:
        t = self.selected_task()
        if t is None:
            self.notify("No task selected.", severity="warning")
            return
        d = self.raw_for(t)

        def done(fields: dict | None) -> None:
            if fields and d is not None:
                d.update(fields)
                self.persist()
                self.notify(f"Updated: {fields['name']}")
        self.push_screen(TaskForm(d), done)

    def action_complete_task(self) -> None:
        t = self.selected_task()
        if t is None:
            self.notify("No task selected.", severity="warning")
            return

        def done(yes: bool | None) -> None:
            if not yes:
                return
            session = load_session()
            if session and session.get("task_id") == t.id:
                clear_session()
            d = self.raw_for(t)
            if d is not None:
                archive_task(d)
            self.raw = [x for x in self.raw if x["id"] != t.id]
            self.persist()
            self.notify(f"Done: {t.name}  (u to undo)")
        self.push_screen(Confirm(f"Mark [bold]{escape(t.name)}[/] as done?\n"
                                 f"[dim]Archived to history -- u undoes.[/]"), done)

    def action_undo(self) -> None:
        entry = pop_history()
        if entry is None:
            self.notify("Nothing to undo.", severity="warning")
            return
        self.raw.append(entry)
        self.persist()
        self.notify(f"Restored: {entry['name']}")

    def action_toggle_timer(self) -> None:
        session = load_session()
        now     = datetime.now()

        # Active session -> stop it and log time (same as `lsf done`)
        if session:
            used_h = _session_elapsed_h(session, now)
            d = next((x for x in self.raw if x["id"] == session["task_id"]), None)
            if d is not None:
                d["time_spent"] = round(d.get("time_spent", 0.0) + used_h, 4)
                self.persist()
                clear_session()
                self.notify(f"Logged {fmt_hours(used_h)} on {d['name']}")
            else:
                clear_session()
                self.notify("Session task no longer exists.", severity="warning")
            self.refresh_timer()
            return

        t = self.selected_task()
        if t is None:
            self.notify("No task selected.", severity="warning")
            return
        save_session(t.id, now.isoformat())
        self.notify(f"Started: {t.name}")
        self.refresh_timer()

    def action_toggle_pause(self) -> None:
        session = load_session()
        if not session:
            self.notify("No active session.", severity="warning")
            return
        now = datetime.now()
        if session.get("paused_at"):
            paused_h = (session.get("paused_h", 0.0)
                        + (now - datetime.fromisoformat(session["paused_at"])
                           ).total_seconds() / 3600)
            save_session(session["task_id"], session["started_at"],
                         paused_h=paused_h, paused_at=None)
            self.notify("Resumed.")
        else:
            save_session(session["task_id"], session["started_at"],
                         paused_h=session.get("paused_h", 0.0),
                         paused_at=now.isoformat())
            self.notify("Paused.")
        self.refresh_timer()

    def action_export_ics(self) -> None:
        if not self.slices:
            self.notify("No sessions to export.", severity="warning")
            return
        n = export_ics(self.slices, DEFAULT_ICS_PATH, datetime.now())
        self.notify(f"Exported {n} session(s) to {DEFAULT_ICS_PATH}")

    def action_panic(self) -> None:
        if not (self.any_impossible or self.collectively_overloaded
                or self.edf_deferred):
            self.notify("No overload detected -- panic not needed.")
            return
        report = build_panic_report(self.tasks, datetime.now())
        self.push_screen(PanicScreen(report))

    def action_reload(self) -> None:
        self.reload_from_disk()
        self.notify("Reloaded.")


def run() -> None:
    LsfApp().run()
