"""A tiny in-memory stand-in for the supabase-py query builder, used only by the tests.

Implements just what the app uses: select / insert / update / delete with eq, neq, in_,
order, limit and execute(), plus the unique constraints and column defaults from the SQL
migration so constraint-handling code paths are exercised for real.
"""
import copy
import uuid
from datetime import datetime, timezone


class FakeAPIError(Exception):
    def __init__(self, message, code="23505"):
        super().__init__(message)
        self.code = code


class Result:
    def __init__(self, data):
        self.data = data


UNIQUE = {
    "users": ["email"],
    "applications": ["user_id", "reference_number", "transaction_id"],
    "audit_log": [],
}

DEFAULTS = {
    "users": {
        "role": "applicant", "email_verified": False, "is_active": True,
        "verification_token_hash": None, "verification_expires": None,
        "reset_token_hash": None, "reset_expires": None,
    },
    "applications": {
        "payment_status": "unpaid", "application_status": "draft", "admin_notes": "",
        "transaction_id": None, "payment_amount": None, "payment_submitted_at": None,
        "submitted_at": None, "resume_filename": None, "resume_original_name": None,
        "phone": "", "date_of_birth": None, "city": "", "state": "", "postal_code": "",
        "country": "", "institution": "", "field_of_study": "", "graduation_year": None,
        "current_employer": "", "job_title": "", "summary": "",
    },
    "audit_log": {"details": {}, "target_type": None, "target_id": None, "ip_address": None, "admin_id": None},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


class FakeDB:
    def __init__(self):
        self.tables = {name: [] for name in UNIQUE}

    def table(self, name):
        return Query(self, name)


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.cols = "select", None, "*"
        self.filters, self._order, self._limit = [], None, None

    # operations
    def select(self, cols="*", **_):
        self.op, self.cols = "select", cols
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    # filters
    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def neq(self, col, val):
        self.filters.append(lambda r: r.get(col) != val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # execution
    def _matching(self):
        return [r for r in self.db.tables[self.name] if all(f(r) for f in self.filters)]

    def _check_unique(self, candidate, ignore=None):
        for col in UNIQUE[self.name]:
            if candidate.get(col) is None:
                continue
            for r in self.db.tables[self.name]:
                if r is not ignore and r.get(col) == candidate[col]:
                    raise FakeAPIError(f'duplicate key value violates unique constraint on "{col}"')

    def execute(self):
        rows = self.db.tables[self.name]
        if self.op == "insert":
            row = {"id": str(uuid.uuid4()), "created_at": _now(), **DEFAULTS[self.name], **copy.deepcopy(self.payload)}
            if self.name != "audit_log":
                row.setdefault("updated_at", row["created_at"])
            self._check_unique(row)
            rows.append(row)
            return Result([copy.deepcopy(row)])
        if self.op == "update":
            out = []
            for r in self._matching():
                candidate = {**r, **copy.deepcopy(self.payload)}
                self._check_unique(candidate, ignore=r)
                r.update(self.payload)
                if "updated_at" in r:
                    r["updated_at"] = _now()
                out.append(copy.deepcopy(r))
            return Result(out)
        if self.op == "delete":
            doomed = self._matching()
            self.db.tables[self.name] = [r for r in rows if r not in doomed]
            return Result([copy.deepcopy(r) for r in doomed])

        matched = self._matching()
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: (r.get(col) is None, r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        if self.cols.strip() != "*":
            wanted = [c.strip() for c in self.cols.split(",")]
            matched = [{c: r.get(c) for c in wanted} for r in matched]
        return Result(copy.deepcopy(matched))
