import frappe
import json
import requests
import os
from urllib.parse import urlparse
from frappe import _
from frappe import ValidationError, _, qb, scrub, throw
from pwa_builder.rename_template_app import rename_template_app
from frappe.model.meta import Meta

@frappe.whitelist()
def add_site(data, update=False):
	if isinstance(data, str):
		data = json.loads(data)
	
	# The remote site should have a dedicated user for this integration
	# with API access enabled. The user provides the API key and secret.
	
	if not update:
		frappe.get_doc({
			"doctype": "PWA-Project",
			"project_title": data.get("project_title"),
			"sub_title": data.get("sub_title"),
			"site_url": data.get("site_url"),
			"api_key": data.get("api_key"),
			"api_secret": data.get("api_secret"),
			"description": data.get("description"),
		}).insert(ignore_permissions=True)
		return "Created"
	else:
		doc = frappe.get_doc("PWA-Project", data.get("name"))
		doc.update({
			"project_title": data.get("project_title"),
			"sub_title": data.get("sub_title"),
			"site_url": data.get("site_url"),
			"api_key": data.get("api_key"),
			"api_secret": data.get("api_secret"),
			"description": data.get("description"),
		})
		doc.save(ignore_permissions=True)
		return "Updated"

@frappe.whitelist()
def get_meta(doctype, project,with_parent=False,cached=True) -> "Meta":
	doc = frappe.get_doc("PWA-Project", project)
	url = urlparse(doc.site_url)
	site_url = url.scheme + "://" + url.netloc
	end_point = "/api/method/frappe.desk.form.load.getdoctype?doctype={0}&with_parent=1".format(doctype)

	response = call(site_url, end_point, doc.api_key, doc.get_password("api_secret"))
	if response.ok:
		meta = response.json()
		if with_parent == True:
			return meta
		else:
			for doc in meta["docs"]:
				if doc["name"] == doctype:
					return doc
	else:
		response.raise_for_status()

@frappe.whitelist()
def get_multiple_meta(project, doctypes):
	"""
	Get metadata for multiple doctypes in a single request.
	NOTE: This is a simulation of the ideal remote API.
	"""
	if isinstance(doctypes, str):
		doctypes = json.loads(doctypes)

	meta_docs = []
	for doctype in doctypes:
		meta_docs.append(get_meta(doctype, project))
	
	return meta_docs

def call(url, end_point, api_key, api_secret):
	headers = {
		"Authorization": f"token {api_key}:{api_secret}"
	}
	response = requests.get(url + end_point, headers=headers)
	response.raise_for_status()
	return response

@frappe.whitelist()
def set_value(doctype, docname, fieldname, value):

	 frappe.set_value(doctype, docname, fieldname, json.dumps(value, indent=4))

@frappe.whitelist()
def get_doc(doctype, docname):
	 return frappe.get_doc(doctype, docname)



# validate form mandatory fields
@frappe.whitelist()
def validate_form_fields(project_name):
	result={
		"success":True,
		"forms_with_missing_fields":{}
	}
	githubDoc = frappe.get_single("PWA GitHub Integration")
	if not (githubDoc and githubDoc.github_username and githubDoc.access_token):
		result['success']=False
		result['message']="Setup PWA GitHub integration First"
		return result
	if form_list := frappe.get_all(
		"PWA DocType", {"project_name": project_name, "disable": 0},["name","doctype_name"]
	):
		for form in form_list:
				
			mandatory_fields_parent = {}
			mandatory_fields_child = {}
			child_table_list=[]
			field_meta = frappe.get_doc("PWA DocType", form.get("name")).get("pwa_form_fields")
			for field in field_meta:
				if form.get('doctype_name') and form.get('doctype_name') == 'Number Card':
					if not (doc := frappe.get_doc(field['fieldtype'], field['fieldname'])):
						field = field.get('fieldtype')
						result['success']=False
						result['message']=f'The Number Card Doctype {field} not found'
				else:
					if field.get("reqd") and field.get("fieldtype") not in ["Column Break","Section Break","Tab Break"]:
						mandatory_fields_parent[field.get("fieldname")] = field.get("label")
					if field.get("fieldtype") == "Table":
						if field.get("options") and isinstance(field.get("options"),list):
							for row in field.get("options"):
								if row.get("reqd"):
									mandatory_fields_child[row.get('parent')] = {}
									mandatory_fields_child[row.get('parent')][row.get("fieldname")]=row.get("label")
									if row.get('parent') not in child_table_list:
										child_table_list.append(row.get('parent'))
						else:
							child_table_list.append(field.get("options"))
					if actual_field_meta := get_meta(doctype=form.get("doctype_name"), project=project_name,with_parent=True,cached=False):
						missing_fields_parent, missing_fields_child = process_mandatory_fields(
							form=form.get("doctype_name"),
							actual_field_meta=actual_field_meta,
							mandatory_fields_parent=mandatory_fields_parent,
							mandatory_fields_child=mandatory_fields_child,
							child_table_list=child_table_list
						)
						if missing_fields_parent.values() or missing_fields_child.values():
							result['success']=False
							result['forms_with_missing_fields'][form.get("doctype_name")]={}
							result['forms_with_missing_fields'][form.get("doctype_name")].update(missing_fields_parent)
							result['forms_with_missing_fields'][form.get("doctype_name")].update(missing_fields_child)
				if not result.get('success'):
					result['message']='Mandatory fields missing in the form/forms'
	else:
		result['success']=False
		result['message']='No PWA DocType found for this project'
	return result
				
def process_mandatory_fields(form, actual_field_meta, mandatory_fields_parent={}, mandatory_fields_child={},child_table_list=[]):
	missing_fields_parent={}
	missing_fields_child={}
	child_table_list = child_table_list or []
	if not isinstance(actual_field_meta, dict):
		actual_field_meta = json.loads(actual_field_meta)
	for doctype_field in actual_field_meta.get('docs'):
		if doctype_field.get('name') == form:
			child_table_list = [child_table.get('options') for child_table in doctype_field.get('fields') if child_table.get('fieldtype') == 'Table' and child_table.get('reqd')]
			if missing_fields:= validate_mandatory_fields(
				actual_field_meta=doctype_field.get('fields'),
				mandatory_fields=mandatory_fields_parent
			):
				missing_fields_parent[form] = list(missing_fields.values())
		elif doctype_field.get('name') in child_table_list:
			if missing_fields := validate_mandatory_fields(
				actual_field_meta=doctype_field.get('fields'),
				mandatory_fields=mandatory_fields_child.get(doctype_field.get('doctype'))
			):
				missing_fields_child[doctype_field.get('name')] = list(missing_fields.values())

	return missing_fields_parent, missing_fields_child

def validate_mandatory_fields(actual_field_meta, mandatory_fields):
	missing_fields = {}
	for field in actual_field_meta:
		if field.get("reqd"):
			if mandatory_fields:
				if field.get("fieldname") not in mandatory_fields.keys():
					missing_fields[field.get("fieldname")] = field.get("label")
			else:
				missing_fields[field.get("fieldname")] = field.get("label")
	return missing_fields