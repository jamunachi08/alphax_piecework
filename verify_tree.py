#!/usr/bin/env python3
"""verify_tree.py - structural guard for alphax_piecework. Run before every push:

    python3 verify_tree.py

Checks: packaging files, module layout, DocType JSON integrity, controller class
names, Link/Table targets, hook dotted paths, page/report/workspace files,
translations CSV, and Python syntax. Exit code 1 on any failure.
"""

import ast
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = "alphax_piecework"
PKG = os.path.join(ROOT, APP)
errors, warnings = [], []

# DocTypes provided by frappe / erpnext v15 that this app links to.
EXTERNAL_DOCTYPES = {
	"Company", "Currency", "UOM", "Item", "Item Group", "Operation", "Workstation", "Workstation Type",
	"Work Order", "Job Card", "Employee", "Department", "User", "Account", "Cost Center", "Journal Entry",
	"Quality Inspection", "DocType",
}
# DocTypes this app must NEVER hard-link (they live in HRMS, an optional app).
FORBIDDEN_LINKS = {"Salary Component", "Additional Salary", "Attendance", "Employee Checkin", "Shift Type"}


def scrub(s):
	return s.replace(" ", "_").replace("-", "_").lower()


def err(msg):
	errors.append(msg)


def check_packaging():
	for rel in ("pyproject.toml", "README.md", "license.txt", f"{APP}/__init__.py", f"{APP}/hooks.py",
				f"{APP}/modules.txt", f"{APP}/patches.txt"):
		if not os.path.exists(os.path.join(ROOT, rel)):
			err(f"missing {rel}")
	init = open(os.path.join(PKG, "__init__.py")).read()
	if not re.search(r'^__version__\s*=\s*"\d+\.\d+\.\d+"', init, re.M):
		err("__version__ missing in package __init__")
	py = open(os.path.join(ROOT, "pyproject.toml")).read()
	for token in ("flit_core", "[tool.bench.frappe-dependencies]", 'dynamic = ["version"]'):
		if token not in py:
			err(f"pyproject.toml missing {token}")
	patches = open(os.path.join(PKG, "patches.txt")).read()
	if "[pre_model_sync]" not in patches or "[post_model_sync]" not in patches:
		err("patches.txt missing section headers")


def modules():
	return [m.strip() for m in open(os.path.join(PKG, "modules.txt")) if m.strip()]


def collect_doctypes():
	found = {}
	for mod in modules():
		base = os.path.join(PKG, scrub(mod), "doctype")
		if not os.path.isdir(os.path.join(PKG, scrub(mod))):
			err(f"module folder missing for {mod}")
			continue
		if not os.path.exists(os.path.join(PKG, scrub(mod), "__init__.py")):
			err(f"module {mod} missing __init__.py")
		for folder in sorted(os.listdir(base)) if os.path.isdir(base) else []:
			path = os.path.join(base, folder)
			if not os.path.isdir(path) or folder.startswith("__"):
				continue
			jpath = os.path.join(path, folder + ".json")
			try:
				meta = json.load(open(jpath))
			except Exception as e:
				err(f"{folder}: bad/missing JSON ({e})")
				continue
			found[meta["name"]] = (mod, path, meta)
	return found


def check_doctypes(found):
	for name, (mod, path, meta) in found.items():
		folder = os.path.basename(path)
		if scrub(name) != folder:
			err(f"{name}: folder {folder} != {scrub(name)}")
		if meta.get("module") != mod:
			err(f"{name}: module '{meta.get('module')}' != '{mod}'")
		for f in ("__init__.py", folder + ".py"):
			if not os.path.exists(os.path.join(path, f)):
				err(f"{name}: missing {f}")
		cls = name.replace(" ", "").replace("-", "")
		src = open(os.path.join(path, folder + ".py")).read()
		if f"class {cls}(" not in src:
			err(f"{name}: controller class {cls} not found")
		fieldnames = [f["fieldname"] for f in meta["fields"]]
		if len(fieldnames) != len(set(fieldnames)):
			err(f"{name}: duplicate fieldnames")
		if meta.get("field_order") and meta["field_order"] != fieldnames:
			err(f"{name}: field_order out of sync")
		if meta.get("is_submittable") and "amended_from" not in fieldnames:
			err(f"{name}: submittable without amended_from")
		if not meta.get("istable") and not meta.get("permissions"):
			err(f"{name}: no permissions")
		if meta.get("istable") and meta.get("permissions"):
			err(f"{name}: child table must not carry permissions")
		for f in meta["fields"]:
			ft, opt = f["fieldtype"], f.get("options")
			if ft in ("Link", "Table", "Table MultiSelect"):
				if not opt:
					err(f"{name}.{f['fieldname']}: {ft} without options")
				elif opt in FORBIDDEN_LINKS:
					err(f"{name}.{f['fieldname']}: hard link to optional HRMS doctype {opt}")
				elif opt not in found and opt not in EXTERNAL_DOCTYPES:
					err(f"{name}.{f['fieldname']}: unknown target {opt}")
				if ft == "Table" and opt in found and not found[opt][2].get("istable"):
					err(f"{name}.{f['fieldname']}: Table target {opt} is not a child table")
			if ft == "Dynamic Link" and opt not in fieldnames:
				err(f"{name}.{f['fieldname']}: Dynamic Link options must name a field")
			if f.get("fetch_from"):
				src_field = f["fetch_from"].split(".")[0]
				if src_field not in fieldnames:
					err(f"{name}.{f['fieldname']}: fetch_from source {src_field} missing")
			if ft == "Button" and f"def {f['fieldname']}(" in src:
				err(f"{name}.{f['fieldname']}: Button fieldname shadows a controller method")
		if meta.get("title_field") and meta["title_field"] not in fieldnames:
			err(f"{name}: title_field missing")
		auto = meta.get("autoname") or ""
		if auto.startswith("field:") and auto[6:] not in fieldnames:
			err(f"{name}: autoname field missing")
		if auto == "naming_series:" and "naming_series" not in fieldnames:
			err(f"{name}: naming_series field missing")


def resolve_dotted(path):
	parts = path.split(".")
	for cut in range(len(parts) - 1, 0, -1):
		mod_file = os.path.join(ROOT, *parts[:cut]) + ".py"
		if os.path.exists(mod_file):
			tree = ast.parse(open(mod_file).read())
			names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
			names |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
			return parts[cut] in names
	return False


def check_hooks():
	src = open(os.path.join(PKG, "hooks.py")).read()
	for dotted in set(re.findall(r'"(alphax_piecework\.[\w\.]+)"', src)):
		if not resolve_dotted(dotted):
			err(f"hooks: cannot resolve {dotted}")
	# whitelisted paths referenced from JS
	for dirpath, _, files in os.walk(PKG):
		for fn in files:
			if fn.endswith(".js"):
				js = open(os.path.join(dirpath, fn)).read()
				for dotted in set(re.findall(r'"(alphax_piecework\.[\w\.]+)"', js)):
					if not resolve_dotted(dotted):
						err(f"{fn}: cannot resolve {dotted}")


def check_pages_reports():
	for mod in modules():
		for kind, exts in (("page", (".json", ".js")), ("report", (".json", ".py", ".js"))):
			base = os.path.join(PKG, scrub(mod), kind)
			if not os.path.isdir(base):
				continue
			for folder in os.listdir(base):
				path = os.path.join(base, folder)
				if not os.path.isdir(path) or folder.startswith("__"):
					continue
				for ext in exts + ("__init__.py",):
					fn = ext if ext == "__init__.py" else folder + ext
					if not os.path.exists(os.path.join(path, fn)):
						err(f"{kind} {folder}: missing {fn}")
				meta = json.load(open(os.path.join(path, folder + ".json")))
				if meta.get("module") != mod:
					err(f"{kind} {folder}: wrong module")
		ws = os.path.join(PKG, scrub(mod), "workspace")
		if os.path.isdir(ws):
			for folder in os.listdir(ws):
				p = os.path.join(ws, folder, folder + ".json")
				if os.path.exists(p):
					meta = json.load(open(p))
					json.loads(meta["content"])


def check_python_and_csv():
	for dirpath, _, files in os.walk(ROOT):
		for fn in files:
			if fn.endswith(".py"):
				try:
					ast.parse(open(os.path.join(dirpath, fn)).read())
				except SyntaxError as e:
					err(f"syntax error {fn}: {e}")
	tr = os.path.join(PKG, "translations", "ar.csv")
	if os.path.exists(tr):
		for i, row in enumerate(csv.reader(open(tr, encoding="utf-8")), 1):
			if len(row) < 2:
				err(f"ar.csv line {i}: needs 2 columns")


if __name__ == "__main__":
	check_packaging()
	found = collect_doctypes()
	check_doctypes(found)
	check_hooks()
	check_pages_reports()
	check_python_and_csv()
	print(f"DocTypes: {len(found)}")
	for w in warnings:
		print("WARN ", w)
	for e in errors:
		print("ERROR", e)
	print("verify_tree: " + ("FAILED" if errors else "OK"))
	sys.exit(1 if errors else 0)
