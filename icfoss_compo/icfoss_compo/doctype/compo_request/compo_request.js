// icfoss_compo/icfoss_compo/doctype/compo_request/compo_request.js
// The server builds the days on save. This script only shows the same result beforehand.

const hhmm = (m) => `${Math.floor((m || 0) / 60)}:${String((m || 0) % 60).padStart(2, "0")}`;

frappe.ui.form.on("Compo Request", {
	onload(frm) {
		if (!frm.is_new()) return;
		if (!frm.doc.from_date) frm.set_value("from_date", frappe.datetime.month_start());
		if (!frm.doc.to_date) frm.set_value("to_date", frappe.datetime.get_today());
		if (!frm.doc.employee) {
			frappe.db.get_value("Employee", { user_id: frappe.session.user }, "name").then((r) => {
				if (r.message && r.message.name) frm.set_value("employee", r.message.name);
			});
		}
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Refresh Days"), () => frm.trigger("load_days"));
		}
	},

	employee(frm) {
		frm.trigger("load_days");
	},
	from_date(frm) {
		frm.trigger("load_days");
	},
	to_date(frm) {
		frm.trigger("load_days");
	},

	load_days(frm) {
		const d = frm.doc;
		if (d.docstatus !== 0 || !d.employee || !d.from_date || !d.to_date) return;

		frappe.call({
			method: "icfoss_compo.icfoss_compo.doctype.compo_request.compo_request.preview_days",
			args: {
				employee: d.employee,
				from_date: d.from_date,
				to_date: d.to_date,
				request_name: frm.is_new() ? null : d.name,
			},
			callback(r) {
				const res = r.message;
				if (!res) return;

				frm.clear_table("days");
				(res.rows || []).forEach((row) => frm.add_child("days", row));
				frm.refresh_field("days");
				frm.set_value("total_available_minutes", res.total_available);
				frm.set_value("allocated_minutes", res.allocated);

				if (res.allocated >= res.required) {
					frm.set_intro(
						__("Available: {0}. This request will use {1} for 1 compensatory off.", [
							hhmm(res.total_available),
							hhmm(res.allocated),
						]),
						"green"
					);
				} else {
					frm.set_intro(
						__("Available: {0}. A compensatory off needs {1}, so this cannot be saved yet.", [
							hhmm(res.total_available),
							hhmm(res.required),
						]),
						"orange"
					);
				}
			},
		});
	},
});