# Copyright (c) 2024, Aerele Technologies Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import os
import json
from pwa_builder.rename_template_app import rename_template_app
from frappe import _, scrub


class PWAProject(Document):
	@frappe.whitelist()
	def export_project(self):
		frappe.enqueue(
			method="pwa_builder.pwa_builder.doctype.pwa_project.pwa_project.schedule_export_project",
			project_name=self.name,
			queue="short",
			job_id=frappe.utils.get_job_name("export_app_for", "PWA-Project", self.name)
		)

def schedule_export_project(project_name):
	pwa_github_integration = frappe.get_single("PWA GitHub Integration")
	
	#project doc
	project_doc = frappe.get_doc("PWA-Project",project_name)

	
	git_clone_response=pwa_github_integration.clone_pwa_template(project_name)
	if git_clone_response.get('success') and git_clone_response.get('public_folder_path'):
		file_path = git_clone_response.get('public_folder_path')
		if project_doc.pwa_theme:
			theme_doc = frappe.get_doc("PWA Theme", project_doc.pwa_theme)
			theme_path = file_path + "/pwa_build/pwa_build/theme.json"
			with open(theme_path, 'w') as theme_file:
				theme_file.write(frappe.as_json(theme_doc))

		if pwa_doctype := frappe.get_list("PWA DocType", {"project_name": project_doc.name}):
			for doctype in pwa_doctype:
				doc = frappe.get_doc("PWA DocType", doctype.name)
				json_data = frappe.as_json(doc.get("pwa_form_fields"))
				file_name = doc.title + ".json"
				path = file_path+"/pwa_build/pwa_build/pwa_form/"+file_name.lower()
				os.makedirs(os.path.dirname(path), exist_ok=True)
				with open(path, 'w') as json_file:
					json_file.write(json_data)
			# rename the app
			if renaming_result := rename_template_app(
				app_path=file_path,
				new_app_name=project_doc.project_title,
				new_url="frontend"
			):
				if renaming_result.get("success"):
					if push_repo_result := pwa_github_integration.push_to_github(
						path=git_clone_response.get('project_folder_path')+"/"+scrub(project_doc.project_title),
						repo_name=project_doc.project_title,
						current_default_branch=project_doc.github_default_branch,
						last_push_commit=project_doc.last_push_commit
					):
						if push_repo_result.get('success'):
							project_doc.github_repository_url = push_repo_result.get("message",{}).get('clone_url',None)
							project_doc.github_default_branch = push_repo_result.get("message",{}).get('default_branch',None)
							project_doc.last_push_commit = push_repo_result.get('commit_msg')
							project_doc.save(ignore_permissions=True)
							return {"success":True, "message":"Project exported successfully"}
						else:
							return {"success" : False, "error" : push_repo_result.get('error')}
					else:
						return {"success":False, "error":"Error while pushing to github"}
				else:
					return {"success" : False, "error" : renaming_result.get('error')}
			else:
				return {"success":False, "error": "Error while renaming app"}
		else:
			return {"success" : False, "error" : "No PWA DocType found for this project"}
	else:
		frappe.log_error(message=_(git_clone_response), title= _("Failed to clone repository"))
		return {"success":False, "error": git_clone_response.get('error')}
