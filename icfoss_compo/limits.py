# icfoss_compo/limits.py
"""
The monthly cap (default 2 days) - ONE rule shared by:
  * Compo Request                 (this app)
  * Compensatory Leave Request    (the standard HRMS doctype, via the validate hook below)
  * Extra Hours Compo Request     (the OLD app, counted while both run side by side)

Pending requests count too (they reserve the quota); cancelled and rejected ones do not.
The error message lists exactly which documents were counted.
"""
import frappe
from frappe import _
from frappe.utils import date_diff, flt, get_first_day, get_last_day, getdate

from icfoss_compo.availability import get_settings

CLR = "Compensatory Leave Request"


def _dead(value):
    value = value or ""
    return value.startswith("Rejected") or value == "Cancelled"


def month_usage(employee, month_date, exclude=None):
    """List of (doctype, name, days) counted against the employee in that month."""
    start, end = get_first_day(month_date), get_last_day(month_date)
    exclude = exclude or (None, None)
    items = []

    # 1. standard Compensatory Leave Requests - counted in days
    meta = frappe.get_meta(CLR)
    state_fields = [f for f in ("status", "workflow_state") if meta.has_field(f)]
    for r in frappe.get_all(
        CLR,
        filters={
            "employee": employee,
            "work_from_date": ["between", [start, end]],
            "docstatus": ["<", 2],
        },
        fields=["name", "work_from_date", "work_end_date", "half_day"] + state_fields,
    ):
        if (CLR, r.name) == exclude or any(_dead(r.get(f)) for f in state_fields):
            continue
        days = date_diff(r.work_end_date, r.work_from_date) + 1 - (0.5 if r.half_day else 0)
        items.append((CLR, r.name, days))

    # 2. Compo Requests of this app
    for r in frappe.get_all(
        "Compo Request",
        filters={"employee": employee, "cap_month": start, "docstatus": ["<", 2]},
        fields=["name", "leave_days", "workflow_state"],
    ):
        if ("Compo Request", r.name) == exclude or _dead(r.workflow_state):
            continue
        items.append(("Compo Request", r.name, flt(r.leave_days) or 1))

    # 3. the old app's requests (only while that doctype still exists)
    if frappe.db.exists("DocType", "Extra Hours Compo Request"):
        for r in frappe.get_all(
            "Extra Hours Compo Request",
            filters={
                "employee": employee,
                "from_date": ["between", [start, end]],
                "compensation_type": ["!=", "Payment"],
                "docstatus": ["<", 2],
            },
            fields=["name", "status"],
        ):
            if not _dead(r.status):
                items.append(("Extra Hours Compo Request", r.name, 1))

    return items


def validate_monthly_cap(employee, month_date, requested=1, exclude=None):
    cap = get_settings().monthly_cap
    items = month_usage(employee, month_date, exclude)
    used = sum(i[2] for i in items)

    if used + flt(requested) > cap:
        details = ", ".join(f"{name} ({days:g} d)" for dt, name, days in items) or "-"
        frappe.throw(
            _(
                "Monthly limit reached: {0} already has {1} compensatory off day(s) counted for {2} "
                "(limit {3}). Counted: {4}"
            ).format(
                employee,
                f"{used:g}",
                getdate(month_date).strftime("%B %Y"),
                f"{cap:g}",
                details,
            ),
            title=_("Compensatory Off Limit"),
        )
    return used


def validate_clr(doc, method=None):
    """validate hook of the standard Compensatory Leave Request: same cap, checked when applying."""
    if doc.docstatus != 0 or not (doc.employee and doc.work_from_date and doc.work_end_date):
        return
    requested = date_diff(doc.work_end_date, doc.work_from_date) + 1 - (0.5 if doc.get("half_day") else 0)
    validate_monthly_cap(
        doc.employee, doc.work_from_date, requested, exclude=(doc.doctype, doc.name)
    )