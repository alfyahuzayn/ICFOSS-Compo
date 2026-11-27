# icfoss_compo/earning.py
"""
Earning side of the extra-hours bank.

When an Attendance is SUBMITTED  -> work out the extra minutes and write/update the Extra Hours Log.
When an Attendance is CANCELLED  -> remove the log that came from it.

Rules (all values come from "Compo Settings", nothing is hard-coded):
  Morning extra : counts only if punch-in  <= Morning Threshold (08:30). Value = Shift Start - in_time
  Evening extra : counts only if punch-out >= Evening Threshold (18:30). Value = out_time - Shift End
  "Count Both Sides Together" ON -> if the employee came early AND left late and the two together
                                    are more than 60 minutes, both count even if neither reached 60.
  No extra on holidays (holiday work uses the normal Compensatory Leave Request).
  No extra for employees ticked "Exclude from Compensatory Off / Extra Hours".
  Only Present / Work From Home attendance counts (Half Day, Absent, On Leave do not).

Safety: the hooks never raise. A bug here must never stop Attendance from being submitted;
errors are written to the Error Log instead.
"""
from datetime import datetime

import frappe
from frappe.utils import cint, get_datetime, get_time, getdate

COUNTED_STATUSES = ("Present", "Work From Home")


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def get_settings():
    s = frappe.get_cached_doc("Compo Settings")
    return frappe._dict(
        shift_start=get_time(s.shift_start or "09:30:00"),
        shift_end=get_time(s.shift_end or "17:30:00"),
        morning_threshold=get_time(s.morning_threshold or "08:30:00"),
        evening_threshold=get_time(s.evening_threshold or "18:30:00"),
        combine=cint(s.count_both_sides_together),
    )


# ---------------------------------------------------------------------------
# calculation
# ---------------------------------------------------------------------------

def compute_minutes(attendance_date, in_time, out_time, st):
    """Returns (morning_minutes, evening_minutes) that COUNT. `st` = get_settings()."""
    day = getdate(attendance_date)
    morning_raw = evening_raw = 0
    morning = evening = 0

    if in_time:
        in_dt = get_datetime(in_time)
        start_dt = datetime.combine(day, st.shift_start)
        if in_dt < start_dt:
            morning_raw = int((start_dt - in_dt).total_seconds() // 60)
            if in_dt <= datetime.combine(day, st.morning_threshold):
                morning = morning_raw

    if out_time:
        out_dt = get_datetime(out_time)
        end_dt = datetime.combine(day, st.shift_end)
        if out_dt > end_dt:
            evening_raw = int((out_dt - end_dt).total_seconds() // 60)
            if out_dt >= datetime.combine(day, st.evening_threshold):
                evening = evening_raw

    if st.combine and morning_raw and evening_raw and morning_raw + evening_raw > 60:
        morning, evening = morning_raw, evening_raw

    return morning, evening


def is_holiday(employee, day):
    from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee

    holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
    return bool(
        holiday_list
        and frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": day})
    )


# ---------------------------------------------------------------------------
# attendance -> log
# ---------------------------------------------------------------------------

def process_attendance(doc):
    """Create / update / remove the Extra Hours Log for one Attendance. Returns a short result word."""
    day = getdate(doc.attendance_date)
    morning = evening = 0

    if (
        doc.status in COUNTED_STATUSES
        and not frappe.db.get_value("Employee", doc.employee, "custom_exclude_from_compo")
        and not is_holiday(doc.employee, day)
    ):
        morning, evening = compute_minutes(day, doc.in_time, doc.out_time, get_settings())

    return _sync_log(doc, day, morning, evening)


def _sync_log(doc, day, morning, evening):
    existing = frappe.db.get_value(
        "Extra Hours Log",
        {"employee": doc.employee, "log_date": day},
        ["name", "source"],
        as_dict=True,
    )

    # HR's manual decision for that day always wins over the automatic calculation
    if existing and existing.source == "Manual":
        return "kept_manual"

    total = morning + evening
    if not total:
        if existing:
            frappe.delete_doc("Extra Hours Log", existing.name, ignore_permissions=True)
            return "removed"
        return "no_extra"

    log = frappe.get_doc("Extra Hours Log", existing.name) if existing else frappe.new_doc("Extra Hours Log")
    log.update(
        {
            "employee": doc.employee,
            "log_date": day,
            "attendance": doc.name,
            "source": "Attendance",
            "morning_minutes": morning,
            "evening_minutes": evening,
            "earned_minutes": total,
        }
    )
    log.flags.ignore_permissions = True
    log.save()
    return "updated" if existing else "created"


# ---------------------------------------------------------------------------
# hooks (see hooks.py)
# ---------------------------------------------------------------------------

def on_attendance_submit(doc, method=None):
    try:
        process_attendance(doc)
    except Exception:
        frappe.log_error(
            title="Extra Hours Log: sync failed on Attendance submit",
            message=f"Attendance: {doc.name}\n\n{frappe.get_traceback()}",
        )


def on_attendance_cancel(doc, method=None):
    try:
        for name in frappe.get_all(
            "Extra Hours Log",
            filters={"attendance": doc.name, "source": "Attendance"},
            pluck="name",
        ):
            frappe.delete_doc("Extra Hours Log", name, ignore_permissions=True)
    except Exception:
        frappe.log_error(
            title="Extra Hours Log: removal failed on Attendance cancel",
            message=f"Attendance: {doc.name}\n\n{frappe.get_traceback()}",
        )


# ---------------------------------------------------------------------------
# one-off: build logs for attendance that already exists
# ---------------------------------------------------------------------------

def backfill(from_date, to_date):
    """
    bench --site <site> execute icfoss_compo.earning.backfill \
        --kwargs '{"from_date": "2026-12-01", "to_date": "2026-12-31"}'

    Safe to run again: it updates existing logs and never overwrites a Manual one.
    Start from your CUT-OVER date so extra hours already claimed in the old app are not counted twice.
    """
    rows = frappe.get_all(
        "Attendance",
        filters={"docstatus": 1, "attendance_date": ["between", [from_date, to_date]]},
        fields=["name", "employee", "attendance_date", "status", "in_time", "out_time"],
        limit_page_length=0,
    )

    counts = {}
    for r in rows:
        try:
            result = process_attendance(r)
        except frappe.ValidationError:
            result = "blocked_in_use"
        counts[result] = counts.get(result, 0) + 1

    frappe.db.commit()
    print(f"Processed {len(rows)} attendance records: {counts}")