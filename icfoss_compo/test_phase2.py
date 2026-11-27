# icfoss_compo/test_phase2.py
"""
Phase 2 scenario test. Everything it creates is ROLLED BACK at the end, so nothing stays in your data.
It uses January 2020 (a month with no real data) and manual logs.

  bench --site erpnext.icfoss.org execute icfoss_compo.test_phase2.run --kwargs '{"employee": "HR-EMP-00179"}'

Pick an employee who is NOT excluded and has no data in January 2020 (almost everyone).
"""
import frappe

from icfoss_compo.availability import get_available_days

FROM, TO = "2020-01-01", "2020-01-31"


def run(employee):
    results = []

    def check(label, ok, detail=""):
        results.append(bool(ok))
        print(("PASS  " if ok else "FAIL  ") + label + (f"   [{detail}]" if detail else ""))

    def make_log(day, minutes):
        return frappe.get_doc(
            {
                "doctype": "Extra Hours Log",
                "employee": employee,
                "log_date": day,
                "source": "Manual",
                "earned_minutes": minutes,
                "reason": "phase 2 test",
            }
        ).insert(ignore_permissions=True)

    def make_request():
        return frappe.get_doc(
            {"doctype": "Compo Request", "employee": employee, "from_date": FROM, "to_date": TO}
        ).insert(ignore_permissions=True)

    def expect_error(label, fn, contains):
        try:
            fn()
            check(label, False, "no error was raised")
        except frappe.ValidationError as e:
            check(label, contains.lower() in str(e).lower(), str(e)[:90])
        except Exception as e:  # noqa
            check(label, False, f"{type(e).__name__}: {str(e)[:90]}")

    if frappe.db.count("Extra Hours Log", {"employee": employee, "log_date": ["between", [FROM, TO]]}):
        print(f"{employee} already has logs in January 2020. Choose another employee.")
        return

    try:
        # 10 hours earned: 4h + 3h + 3h
        l1 = make_log("2020-01-06", 240)
        make_log("2020-01-07", 180)
        l3 = make_log("2020-01-08", 180)

        r1 = make_request()
        use = [(d.log_date.strftime("%d"), d.use_minutes) for d in r1.days]
        check(
            "request 1 takes 8h oldest-first (240 + 180 + 60)",
            r1.allocated_minutes == 480 and [m for _d, m in use] == [240, 180, 60],
            str(use),
        )

        check(
            "2h (120 min) is left over",
            get_available_days(employee, FROM, TO).total_available == 120,
        )

        expect_error("second request with only 2h is refused", make_request, "needs")

        # 6 more hours: 4h + 2h -> 2h leftover + 6h = 8h
        make_log("2020-01-09", 240)
        make_log("2020-01-12", 120)
        r2 = make_request()
        use2 = [(d.log_date.strftime("%d"), d.use_minutes) for d in r2.days]
        check(
            "request 2 = leftover 2h + new 6h (120 + 240 + 120)",
            r2.allocated_minutes == 480 and [m for _d, m in use2] == [120, 240, 120],
            str(use2),
        )

        # plenty of hours, but the monthly cap (2) is used up
        make_log("2020-01-13", 480)
        expect_error("third request is blocked by the monthly cap", make_request, "monthly limit")

        # cancelling request 1 frees its minutes AND one cap slot
        r1.submit()
        r1.cancel()
        r3 = make_request()
        check("after cancelling request 1, a new request is allowed", r3.allocated_minutes == 480)

        # protection of logs that are in use
        expect_error(
            "a log used by a request cannot be deleted",
            lambda: frappe.delete_doc("Extra Hours Log", l3.name, ignore_permissions=True),
            "cannot be deleted",
        )

        def reduce_log():
            doc = frappe.get_doc("Extra Hours Log", l3.name)
            doc.earned_minutes = 30
            doc.save(ignore_permissions=True)

        expect_error("earned minutes cannot drop below what is used", reduce_log, "cannot be reduced")

        # the days table cannot be edited by hand
        def tamper():
            doc = frappe.get_doc("Compo Request", r2.name)
            doc.days[0].use_minutes = 1
            doc.save(ignore_permissions=True)

        expect_error("days table cannot be edited by hand", tamper, "cannot be edited")

    finally:
        frappe.db.rollback()
        print("\nAll test data was rolled back.")

    print(f"{sum(results)} of {len(results)} checks passed.")