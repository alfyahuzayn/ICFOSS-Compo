# icfoss_compo/availability.py
"""
The extra-hours bank.

For every Extra Hours Log (one employee, one day):

    available = earned_minutes - minutes used by OTHER live Compo Requests

"Live" = not cancelled (docstatus < 2) and not rejected by the workflow.
Nothing is copied from request to request: a leftover simply shows up as "available"
the next time, and the same minute can never be claimed twice.

A request takes the OLDEST days first until it has exactly `minutes_per_leave` (480).
"""
import frappe
from frappe.utils import cint, flt


def get_settings():
    s = frappe.get_cached_doc("Compo Settings")
    return frappe._dict(
        minutes_per_leave=cint(s.minutes_per_compensatory_off) or 480,
        monthly_cap=flt(s.max_compo_days_per_month) or 2,
        carry_over_months=cint(s.carry_over_months),
        leave_type=s.leave_type or "Compensatory Off",
    )


def hhmm(minutes):
    minutes = cint(minutes)
    return f"{minutes // 60}:{minutes % 60:02d}"


def get_used_by_log(log_names, exclude_request=None):
    """{log name: minutes already used by live Compo Requests (except `exclude_request`)}"""
    log_names = tuple(log_names)
    if not log_names:
        return {}
    rows = frappe.db.sql(
        """
        SELECT d.log AS log, SUM(d.use_minutes) AS used
        FROM `tabCompo Request Day` d
        JOIN `tabCompo Request` p
          ON p.name = d.parent AND d.parenttype = 'Compo Request'
        WHERE d.log IN %(logs)s
          AND p.docstatus < 2
          AND IFNULL(p.workflow_state, '') NOT LIKE 'Rejected%%'
          AND p.name != %(exclude)s
        GROUP BY d.log
        """,
        {"logs": log_names, "exclude": exclude_request or ""},
        as_dict=True,
    )
    return {r.log: cint(r.used) for r in rows}


def get_available_days(employee, from_date, to_date, exclude_request=None, lock=False):
    """
    Returns frappe._dict(
        rows            = days this request would use, oldest first (list of dicts, child-table shaped),
        total_available = all available minutes in the date range,
        allocated       = minutes the rows add up to (max = minutes_per_leave),
        required        = minutes_per_leave,
    )
    lock=True takes a row lock so two simultaneous requests cannot take the same minutes.
    """
    st = get_settings()

    if lock:
        frappe.db.sql(
            """SELECT name FROM `tabExtra Hours Log`
               WHERE employee = %s AND log_date BETWEEN %s AND %s FOR UPDATE""",
            (employee, from_date, to_date),
        )

    logs = frappe.get_all(
        "Extra Hours Log",
        filters={"employee": employee, "log_date": ["between", [from_date, to_date]]},
        fields=["name", "log_date", "earned_minutes"],
        order_by="log_date asc, name asc",
        limit_page_length=0,
    )
    used = get_used_by_log([l.name for l in logs], exclude_request)

    candidates, total = [], 0
    for l in logs:
        already = used.get(l.name, 0)
        available = max(cint(l.earned_minutes) - already, 0)
        if available > 0:
            candidates.append((l, already, available))
            total += available

    remaining = st.minutes_per_leave
    rows = []
    for l, already, available in candidates:
        if remaining <= 0:
            break
        use = min(available, remaining)
        remaining -= use
        rows.append(
            {
                "log": l.name,
                "log_date": l.log_date,
                "earned_minutes": cint(l.earned_minutes),
                "used_elsewhere": already,
                "available_minutes": available,
                "use_minutes": use,
            }
        )

    return frappe._dict(
        rows=rows,
        total_available=total,
        allocated=st.minutes_per_leave - remaining,
        required=st.minutes_per_leave,
    )