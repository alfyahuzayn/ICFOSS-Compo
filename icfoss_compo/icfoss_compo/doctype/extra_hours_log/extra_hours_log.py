# icfoss_compo/icfoss_compo/doctype/extra_hours_log/extra_hours_log.py
"""
Extra Hours Log: one record per employee per day = minutes earned that day.

Source = Attendance : created / updated automatically by icfoss_compo.earning
Source = Manual     : created by HR, a reason is mandatory

Nothing else in the system stores "hours". Availability is always calculated as
    earned_minutes (this log) - minutes used by live Compo Requests
so there is no number that can get out of sync.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, nowdate


class ExtraHoursLog(Document):
    def validate(self):
        self.validate_date()
        self.set_minutes()
        self.validate_manual_entry()

    def validate_date(self):
        if self.log_date and getdate(self.log_date) > getdate(nowdate()):
            frappe.throw(_("Log Date cannot be in the future."))

    def set_minutes(self):
        if self.source == "Attendance":
            # always derived, never typed
            self.earned_minutes = cint(self.morning_minutes) + cint(self.evening_minutes)
        else:
            self.morning_minutes = 0
            self.evening_minutes = 0

    def validate_manual_entry(self):
        if self.source == "Manual" and not (self.reason or "").strip():
            frappe.throw(_("A reason is mandatory for a manual Extra Hours Log."))

        if cint(self.earned_minutes) <= 0:
            frappe.throw(_("Earned Minutes must be greater than zero."))