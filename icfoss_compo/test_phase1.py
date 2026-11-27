# icfoss_compo/test_phase1.py
"""
Phase 1 tests. TEST SITE ONLY (refuses to run unless developer_mode is on).

1) Scenario tests - creates its own Attendance records, checks the Extra Hours Log, then deletes everything:
   bench --site compo-dev.local execute icfoss_compo.test_phase1.run --kwargs '{"employee": "HR-EMP-00001"}'

2) Comparison with the old app (read-only, changes nothing):
   bench --site compo-dev.local execute icfoss_compo.test_phase1.compare --kwargs '{"from_date": "2026-11-01", "to_date": "2026-11-20"}'
"""
import frappe
from frappe.utils import add_days, getdate, nowdate

from icfoss_compo.earning import COUNTED_STATUSES, compute_minutes, get_settings, is_holiday


def _free_days(employee, wanted, holiday=False):
    """Past dates with no attendance / leave for this employee (holidays or working days)."""
    joined = frappe.db.get_value("Employee", employee, "date_of_joining")
    found, day = [], add_days(getdate(nowdate()), -1)
    for _i in range(200):
        if joined and day < getdate(joined):
            break
        busy = frappe.db.exists(
            "Attendance", {"employee": employee, "attendance_date": day, "docstatus": ["<", 2]}
        )
        on_leave = frappe.db.exists(
            "Leave Application",
            {"employee": employee, "docstatus": 1, "from_date": ["<=", day], "to_date": [">=", day]},
        )
        if not busy and not on_leave and is_holiday(employee, day) == holiday:
            found.append(day)
            if len(found) == wanted:
                break
        day = add_days(day, -1)
    return found


def run(employee):
    if not frappe.conf.developer_mode:
        print("STOP: developer_mode is off. Run this on the TEST site only.")
        return

    st = get_settings()
    work = _free_days(employee, 7)
    holiday = _free_days(employee, 1, holiday=True)
    if len(work) < 7:
        print(f"Need 7 days without attendance for {employee}, found {len(work)}. Try another employee.")
        return

    flag_before = frappe.db.get_value("Employee", employee, "custom_exclude_from_compo") or 0
    created, results = [], []

    def make(day, in_, out):
        att = frappe.get_doc(
            {
                "doctype": "Attendance",
                "employee": employee,
                "attendance_date": day,
                "status": "Present",
                "in_time": f"{day} {in_}:00",
                "out_time": f"{day} {out}:00",
            }
        )
        att.insert(ignore_permissions=True)
        att.submit()
        created.append(att.name)
        return att

    def log_for(day):
        return frappe.db.get_value(
            "Extra Hours Log",
            {"employee": employee, "log_date": day},
            ["name", "source", "morning_minutes", "evening_minutes", "earned_minutes"],
            as_dict=True,
        )

    def record(label, ok, detail=""):
        results.append(bool(ok))
        print(("PASS  " if ok else "FAIL  ") + label + (f"   [{detail}]" if detail else ""))

    first = None
    try:
        cases = [
            ("in 08:30 / out 17:30  -> morning 60", "08:30", "17:30", (60, 0)),
            ("in 08:45 / out 17:30  -> nothing", "08:45", "17:30", (0, 0)),
            ("in 09:30 / out 18:30  -> evening 60", "09:30", "18:30", (0, 60)),
            ("in 08:30 / out 18:30  -> 60 + 60", "08:30", "18:30", (60, 60)),
            (
                "in 09:00 / out 18:20  -> " + ("30 + 50 (combine ON)" if st.combine else "nothing"),
                "09:00",
                "18:20",
                (30, 50) if st.combine else (0, 0),
            ),
        ]
        for day, (label, in_, out, expected) in zip(work, cases):
            try:
                att = make(day, in_, out)
                if first is None:
                    first = (att, day)
                lg = log_for(day)
                got = (lg.morning_minutes, lg.evening_minutes) if lg else (0, 0)
                record(label, got == expected, f"got {got}")
            except Exception as e:
                record(label, False, f"ERROR {e}")

        # excluded employee
        try:
            frappe.db.set_value("Employee", employee, "custom_exclude_from_compo", 1)
            make(work[5], "08:30", "18:30")
            record("excluded employee earns nothing", log_for(work[5]) is None)
        except Exception as e:
            record("excluded employee earns nothing", False, f"ERROR {e}")
        finally:
            frappe.db.set_value("Employee", employee, "custom_exclude_from_compo", flag_before)

        # manual log must win
        try:
            frappe.get_doc(
                {
                    "doctype": "Extra Hours Log",
                    "employee": employee,
                    "log_date": work[6],
                    "source": "Manual",
                    "earned_minutes": 45,
                    "reason": "phase 1 test",
                }
            ).insert(ignore_permissions=True)
            make(work[6], "08:30", "18:30")
            lg = log_for(work[6])
            record("manual log is not overwritten", lg and lg.source == "Manual" and lg.earned_minutes == 45)
        except Exception as e:
            record("manual log is not overwritten", False, f"ERROR {e}")

        # holiday
        if holiday:
            try:
                make(holiday[0], "08:30", "18:30")
                record("holiday earns nothing", log_for(holiday[0]) is None)
            except Exception as e:
                record("holiday earns nothing", False, f"ERROR {e}")
        else:
            print("SKIP  holiday test (no free holiday found for this employee)")

        # cancel removes the log
        if first:
            att, day = first
            try:
                att.cancel()
                record("cancelling the attendance removes its log", log_for(day) is None)
            except Exception as e:
                record("cancelling the attendance removes its log", False, f"ERROR {e}")

    finally:
        frappe.db.set_value("Employee", employee, "custom_exclude_from_compo", flag_before)
        for name in created:
            att = frappe.get_doc("Attendance", name)
            if att.docstatus == 1:
                att.cancel()
            frappe.delete_doc("Attendance", name, force=True, ignore_permissions=True)
        frappe.db.delete("Extra Hours Log", {"employee": employee, "log_date": ["in", work + holiday]})
        frappe.db.commit()

    print(f"\n{sum(results)} of {len(results)} checks passed. Test records were removed.")


def compare(from_date, to_date):
    """Old app's custom__total_extra_working_minutes vs. what the new rules give. Read-only."""
    st = get_settings()
    excluded = {}
    rows = frappe.get_all(
        "Attendance",
        filters={"docstatus": 1, "attendance_date": ["between", [from_date, to_date]]},
        fields=[
            "name", "employee", "attendance_date", "status",
            "in_time", "out_time", "custom__total_extra_working_minutes",
        ],
        limit_page_length=0,
    )

    diffs = []
    for r in rows:
        if r.employee not in excluded:
            excluded[r.employee] = frappe.db.get_value("Employee", r.employee, "custom_exclude_from_compo")
        new = 0
        if (
            r.status in COUNTED_STATUSES
            and not excluded[r.employee]
            and not is_holiday(r.employee, getdate(r.attendance_date))
        ):
            m, e = compute_minutes(r.attendance_date, r.in_time, r.out_time, st)
            new = m + e
        old = int(r.custom__total_extra_working_minutes or 0)
        if new != old:
            diffs.append((r.name, r.employee, r.attendance_date, r.status, f"old={old}", f"new={new}"))

    print(f"{len(rows)} attendance records compared, {len(diffs)} differ.")
    for d in diffs[:40]:
        print(" | ".join(str(x) for x in d))
