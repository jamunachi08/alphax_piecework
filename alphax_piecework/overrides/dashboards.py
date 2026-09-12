from frappe import _


def _add(data, label, items):
	data = data or {}
	data.setdefault("transactions", [])
	for group in data["transactions"]:
		if group.get("label") == label:
			group["items"] = list(dict.fromkeys(group["items"] + items))
			return data
	data["transactions"].append({"label": label, "items": items})
	return data


def work_order(data):
	return _add(data, _("PieceWork"), ["Piece Rate Log", "Shop Floor QA Log"])


def employee(data):
	return _add(data, _("PieceWork"), ["Piece Rate Log", "Employee Operation Skill"])
