app_name = "gdn_rag"
app_title = "GDN RAG"
app_publisher = "GDN"
app_description = "RAG for gdn apps"
app_email = "erp@gdn.int"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "gdn_rag",
# 		"logo": "/assets/gdn_rag/logo.png",
# 		"title": "GDN RAG",
# 		"route": "/gdn_rag",
# 		"has_permission": "gdn_rag.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/gdn_rag/css/gdn_rag.css"
# app_include_js = "/assets/gdn_rag/js/gdn_rag.js"

# include js, css files in header of web template
# web_include_css = "/assets/gdn_rag/css/gdn_rag.css"
# web_include_js = "/assets/gdn_rag/js/gdn_rag.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "gdn_rag/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "gdn_rag/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "gdn_rag.utils.jinja_methods",
# 	"filters": "gdn_rag.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "gdn_rag.install.before_install"
# after_install = "gdn_rag.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "gdn_rag.uninstall.before_uninstall"
# after_uninstall = "gdn_rag.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "gdn_rag.utils.before_app_install"
# after_app_install = "gdn_rag.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "gdn_rag.utils.before_app_uninstall"
# after_app_uninstall = "gdn_rag.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "gdn_rag.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"gdn_rag.tasks.all"
# 	],
# 	"daily": [
# 		"gdn_rag.tasks.daily"
# 	],
# 	"hourly": [
# 		"gdn_rag.tasks.hourly"
# 	],
# 	"weekly": [
# 		"gdn_rag.tasks.weekly"
# 	],
# 	"monthly": [
# 		"gdn_rag.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "gdn_rag.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "gdn_rag.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "gdn_rag.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["gdn_rag.utils.before_request"]
# after_request = ["gdn_rag.utils.after_request"]

# Job Events
# ----------
# before_job = ["gdn_rag.utils.before_job"]
# after_job = ["gdn_rag.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"gdn_rag.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

