# icfoss_compo/install.py
"""
Creates the tick box on the Employee form that replaces the old hard-coded list of names.
Safe to run any number of times.

  bench --site <site> execute icfoss_compo.install.after_install
"""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
    create_custom_fields(
        {
            "Employee": [
                {
                    "fieldname": "custom_exclude_from_compo",
                    "label": "Exclude from Compensatory Off / Extra Hours",
                    "fieldtype": "Check",
                    "default": "0",
                    "insert_after": "holiday_list",
                    "description": "Tick for employees who do not earn extra hours / compensatory off.",
                }
            ]
        }
    )