"""Vivekananda College DOC Portal - Flask + SQLite backend.

All security decisions (who can see / upload / comment) are made HERE on the server.
The browser code in /static is only a user interface.
"""
import csv, io, os, re, secrets, sqlite3, tempfile, time, zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, after_this_request, g, jsonify,
                   request, send_file, send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "portal.db")
os.makedirs(UPLOAD_DIR, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))
MAX_FILE_MB = 15
MAX_FILES_PER_UPLOAD = 10
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "doc", "docx", "xls",
               "xlsx", "ppt", "pptx", "txt", "csv"}
DOC_TYPES = ["Photograph / Image", "PDF / Report", "Meeting Minutes",
             "Action Plan / Achievement", "Notice / Circular",
             "Attendance / Participant List", "Other Supporting Document"]
STATUS_LABEL = {"submitted": "Pending review", "approved": "Approved",
                "revision": "Changes requested"}


def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    p = os.path.join(DATA_DIR, ".secret_key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip()


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(
    SECRET_KEY=_secret_key(),
    MAX_CONTENT_LENGTH=(MAX_FILE_MB * MAX_FILES_PER_UPLOAD + 5) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SEND_FILE_MAX_AGE_DEFAULT=0,
)

# --------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN('admin','member')), password_hash TEXT NOT NULL,
  must_change INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, last_login TEXT);
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER REFERENCES units(id),
  code TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, quarterly INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS user_units(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id, unit_id));
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id INTEGER NOT NULL REFERENCES units(id),
  academic_year TEXT NOT NULL, quarter TEXT, doc_date TEXT NOT NULL, title TEXT NOT NULL,
  doc_type TEXT NOT NULL, submitted_by TEXT, notes TEXT,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'submitted', admin_seen INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_doc_unit ON documents(unit_id);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  orig_name TEXT NOT NULL, stored_name TEXT NOT NULL, mime TEXT, size INTEGER NOT NULL,
  uploaded_by INTEGER REFERENCES users(id), uploaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id), is_admin INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'comment', new_status TEXT, body TEXT NOT NULL DEFAULT '',
  seen_by_dept INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT, doc_date TEXT NOT NULL,
  academic_year TEXT NOT NULL, all_units INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notice_targets(
  notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE, PRIMARY KEY(notice_id, unit_id));
CREATE TABLE IF NOT EXISTS notice_files(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  orig_name TEXT NOT NULL, stored_name TEXT NOT NULL, size INTEGER NOT NULL, uploaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notice_reads(
  notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  read_at TEXT NOT NULL, PRIMARY KEY(notice_id, user_id));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_email TEXT, action TEXT NOT NULL,
  detail TEXT, ip TEXT, created_at TEXT NOT NULL);
"""

# Structure taken from "VC_DOC Portal_12.11.2026.docx".  (code, name, [children])
SEED = [
    ("1", "IQAC", [
        ("1.1", "IQAC Composition (Committee Members)", []),
        ("1.2", "Action Plan & Achievements", [], {"quarterly": 1}),
        ("1.3", "Meeting Minutes", []),
        ("1.4", "IQAC Organized Programs", []),
        ("1.5", "AQAR", [(f"1.5.{i}", f"Criteria-{r}", []) for i, r in
                         enumerate(["I", "II", "III", "IV", "V", "VI", "VII"], 1)]),
    ]),
    ("2", "Department", [
        ("2.1", "Arts", [(f"2.1.{i}", n, []) for i, n in enumerate(
            ["Bengali", "English", "History", "Human Rights", "Journalism & Mass Com",
             "Philosophy", "Political Science", "Film Studies", "Women Studies"], 1)]),
        ("2.2", "Commerce", []),
        # The college document shows "……" between Computer Science and Zoology
        # (2.3.3 - 2.3.11). Add the remaining departments in Admin > Units.
        ("2.3", "Science", [("2.3.1", "Chemistry", []), ("2.3.2", "Computer Science", []),
                            ("2.3.12", "Zoology", [])]),
    ]),
    ("3", "Cells / Committees", [(f"3.{i}", n, []) for i, n in enumerate(
        ["Admission Sub-Committee", "Academic Sub-Committee", "Examination Sub-Committee",
         "R & D Sub-Committee", "IIC", "Career Counselling & Placement Sub-Committee",
         "Psychological Counselling Cell", "Cultural Sub-Committee",
         "Sports, Yoga, Gymnasium Sub-Committee"], 1)]),
    ("", "College Office", []),
    ("", "Finance", []),
    ("", "Library", []),
]


def now():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now(IST).strftime("%Y-%m-%d")


def current_academic_year():
    d = datetime.now(IST)
    y = d.year if d.month >= 6 else d.year - 1      # academic year starts in June
    return f"{y}-{str(y + 1)[2:]}"


def academic_years():
    y0 = int(current_academic_year()[:4])
    return [f"{y}-{str(y + 1)[2:]}" for y in range(y0 + 1, 2019, -1)]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def _seed_units(db, items, parent=None):
    for it in items:
        code, name, kids = it[0], it[1], it[2]
        extra = it[3] if len(it) > 3 else {}
        cur = db.execute("INSERT INTO units(parent_id,code,name,quarterly) VALUES(?,?,?,?)",
                         (parent, code, name, extra.get("quarterly", 0)))
        _seed_units(db, kids, cur.lastrowid)


def random_password(n=12):
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM units LIMIT 1").fetchone():
        _seed_units(db, SEED)
    if not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        email = os.environ.get("ADMIN_EMAIL", "principal@college.example").lower()
        pw = os.environ.get("ADMIN_PASSWORD") or random_password(14)
        db.execute("INSERT INTO users(email,name,role,password_hash,must_change,created_at)"
                   " VALUES(?,?,?,?,1,?)",
                   (email, "Principal / Administrator", "admin", generate_password_hash(pw), now()))
        print("=" * 60, f"\nFIRST-RUN ADMIN ACCOUNT\n  email:    {email}\n  password: {pw}\n"
              "  (you must change it at first login)\n" + "=" * 60, flush=True)
    db.commit()
    db.close()


init_db()

# ------------------------------------------------------------------ units/tree
def code_key(code):
    parts = []
    for p in (code or "").split("."):
        parts.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return parts or [(2, 0, "")]


def unit_tree(db):
    rows = [dict(r) for r in db.execute("SELECT * FROM units")]
    by_id = {r["id"]: r for r in rows}
    kids = {}
    for r in rows:
        kids.setdefault(r["parent_id"], []).append(r["id"])
    for k in kids:
        kids[k].sort(key=lambda i: (by_id[i]["code"] == "", code_key(by_id[i]["code"]), i))
    return by_id, kids


def descendants(kids, uid):
    out, stack = [], [uid]
    while stack:
        x = stack.pop()
        out.append(x)
        stack.extend(kids.get(x, []))
    return out


def unit_path(by_id, uid):
    parts = []
    while uid:
        parts.append(by_id[uid]["name"])
        uid = by_id[uid]["parent_id"]
    return " › ".join(reversed(parts))


def accessible_units(db, user):
    """Set of unit ids this user may open (assigned units + everything below them)."""
    by_id, kids = unit_tree(db)
    if user["role"] == "admin":
        return set(by_id)
    assigned = [r[0] for r in db.execute("SELECT unit_id FROM user_units WHERE user_id=?", (user["id"],))]
    out = set()
    for a in assigned:
        if a in by_id:
            out.update(descendants(kids, a))
    return out


def can_upload(db, user, unit_id):
    if user["role"] != "member":          # the principal reviews; units upload
        return False
    by_id, kids = unit_tree(db)
    u = by_id.get(unit_id)
    return bool(u and u["active"] and not kids.get(unit_id) and unit_id in accessible_units(db, user))

# ------------------------------------------------------------------- security
_attempts = {}


def audit(action, detail="", email=None):
    u = current_user()
    get_db().execute("INSERT INTO audit(user_email,action,detail,ip,created_at) VALUES(?,?,?,?,?)",
                     (email or (u["email"] if u else None), action, detail[:500],
                      request.headers.get("X-Forwarded-For", request.remote_addr), now()))
    get_db().commit()


def current_user():
    if "_user" in g:
        return g._user
    uid = session.get("uid")
    row = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone() if uid else None
    g._user = dict(row) if row else None
    return g._user


def err(msg, status=400, **extra):
    return jsonify(error=msg, **extra), status


@app.before_request
def guard():
    if not request.path.startswith("/api/") or request.path == "/api/login":
        return
    u = current_user()
    if not u:
        return err("Please sign in again.", 401)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf", "x")):
            return err("Security token mismatch. Refresh the page.", 403)
    if u["must_change"] and request.path not in ("/api/me", "/api/logout", "/api/change-password"):
        return err("You must set a new password first.", 403, code="password_change_required")


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
                            "script-src 'self'; frame-ancestors 'none'; object-src 'self'")
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def admin_only(f):
    @wraps(f)
    def w(*a, **k):
        if current_user()["role"] != "admin":
            return err("Administrator access required.", 403)
        return f(*a, **k)
    return w


def public_user(u):
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"],
            "must_change": bool(u["must_change"])}


def strong_enough(pw):
    return len(pw) >= 8 and re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)

# ----------------------------------------------------------------------- pages
@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")

# ------------------------------------------------------------------------ auth
@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    email, pw = str(d.get("email", "")).strip().lower(), str(d.get("password", ""))
    key = (email, request.remote_addr)
    tries = [t for t in _attempts.get(key, []) if t > time.time() - 900]
    if len(tries) >= 6:
        return err("Too many failed attempts. Please try again in 15 minutes.", 429)
    row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok = check_password_hash(row["password_hash"] if row else generate_password_hash("x"), pw)
    if not (row and ok and row["active"]):
        _attempts[key] = tries + [time.time()]
        audit("login_failed", email, email=email)
        return err("The email address or password is incorrect.", 401)
    _attempts.pop(key, None)
    session.clear()
    session.permanent = True
    session["uid"], session["csrf"] = row["id"], secrets.token_hex(16)
    get_db().execute("UPDATE users SET last_login=? WHERE id=?", (now(), row["id"]))
    g._user = dict(row)
    audit("login")
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    audit("logout")
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(user=public_user(current_user()), csrf=session["csrf"], today=today(),
                   years=academic_years(), current_year=current_academic_year(),
                   doc_types=DOC_TYPES, status_labels=STATUS_LABEL, max_file_mb=MAX_FILE_MB)


@app.post("/api/change-password")
def change_password():
    u, d = current_user(), request.get_json(silent=True) or {}
    if not check_password_hash(u["password_hash"], str(d.get("current", ""))):
        return err("Current password is incorrect.")
    new = str(d.get("new", ""))
    if not strong_enough(new):
        return err("New password must be at least 8 characters and contain letters and numbers.")
    if new == d.get("current"):
        return err("Choose a password different from the temporary one.")
    get_db().execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?",
                     (generate_password_hash(new), u["id"]))
    get_db().commit()
    audit("password_changed")
    return jsonify(ok=True)

# ------------------------------------------------------------------- units API
@app.get("/api/units")
def units():
    db, u = get_db(), current_user()
    by_id, kids = unit_tree(db)
    acc = accessible_units(db, u)
    # ancestors of accessible units are shown as non-clickable folders
    shown = set(acc)
    for i in acc:
        p = by_id[i]["parent_id"]
        while p and p not in shown:
            shown.add(p)
            p = by_id[p]["parent_id"]
    if u["role"] != "admin":
        shown = {i for i in shown if by_id[i]["active"]}
    own_docs = {r[0]: r[1] for r in db.execute("SELECT unit_id,COUNT(*) FROM documents GROUP BY unit_id")}
    if u["role"] == "admin":
        badge_rows = db.execute("SELECT unit_id,COUNT(*) FROM documents WHERE admin_seen=0 GROUP BY unit_id")
    else:
        badge_rows = db.execute("SELECT d.unit_id,COUNT(*) FROM comments c JOIN documents d ON d.id=c.doc_id"
                                " WHERE c.is_admin=1 AND c.seen_by_dept=0 GROUP BY d.unit_id")
    own_badge = {r[0]: r[1] for r in badge_rows}
    out = []

    def walk(pid):
        for i in kids.get(pid, []):
            if i not in shown:
                continue
            sub = descendants(kids, i)
            r = by_id[i]
            out.append({"id": i, "parent_id": r["parent_id"], "code": r["code"], "name": r["name"],
                        "quarterly": bool(r["quarterly"]), "active": bool(r["active"]),
                        "has_children": bool(kids.get(i)), "accessible": i in acc,
                        "uploadable": can_upload(db, u, i),
                        "doc_count": sum(own_docs.get(s, 0) for s in sub) if i in acc else 0,
                        "badge": sum(own_badge.get(s, 0) for s in sub) if i in acc else 0})
            walk(i)
    walk(None)
    return jsonify(units=out)

# ---------------------------------------------------------------- documents API
def _doc_rows(db, user, args, limit=500):
    by_id, kids = unit_tree(db)
    acc = accessible_units(db, user)
    scope = acc
    if args.get("unit_id"):
        uid = int(args["unit_id"])
        if uid not in acc:
            abort(403)
        scope = set(descendants(kids, uid)) & acc
    if not scope:
        return []
    where, params = [f"d.unit_id IN ({','.join('?' * len(scope))})"], list(scope)
    if args.get("year"):
        where.append("d.academic_year=?"); params.append(args["year"])
    if args.get("status"):
        where.append("d.status=?"); params.append(args["status"])
    col = "d.doc_date" if args.get("basis") == "activity" else "substr(d.created_at,1,10)"
    if args.get("from"):
        where.append(f"{col}>=?"); params.append(args["from"])
    if args.get("to"):
        where.append(f"{col}<=?"); params.append(args["to"])
    if args.get("q"):
        where.append("(d.title LIKE ? OR d.notes LIKE ? OR d.submitted_by LIKE ?)")
        params += [f"%{args['q']}%"] * 3
    if args.get("new") == "1":
        where.append("d.admin_seen=0" if user["role"] == "admin" else
                     "EXISTS(SELECT 1 FROM comments c WHERE c.doc_id=d.id AND c.is_admin=1 AND c.seen_by_dept=0)")
    sql = f"""SELECT d.*, o.name AS owner_name,
      (SELECT COUNT(*) FROM files f WHERE f.doc_id=d.id) AS file_count,
      (SELECT COUNT(*) FROM comments c WHERE c.doc_id=d.id AND c.kind='comment') AS comment_count,
      (SELECT COUNT(*) FROM comments c WHERE c.doc_id=d.id AND c.is_admin=1 AND c.seen_by_dept=0) AS unseen_remarks,
      (SELECT group_concat(f.orig_name,'; ') FROM files f WHERE f.doc_id=d.id) AS file_names
      FROM documents d JOIN users o ON o.id=d.owner_id WHERE {' AND '.join(where)}
      ORDER BY d.created_at DESC, d.id DESC LIMIT {int(limit)}"""
    rows = []
    for r in db.execute(sql, params):
        r = dict(r)
        r["unit_path"] = unit_path(by_id, r["unit_id"])
        r["unit_name"] = by_id[r["unit_id"]]["name"]
        r["is_new"] = (not r["admin_seen"]) if user["role"] == "admin" else r["unseen_remarks"] > 0
        r["can_delete"] = can_delete(user, r)
        rows.append(r)
    return rows


def can_delete(user, d):
    if user["role"] == "admin":
        return True
    if d["owner_id"] != user["id"] or d["status"] != "submitted":
        return False
    return not get_db().execute("SELECT 1 FROM comments WHERE doc_id=? AND is_admin=1", (d["id"],)).fetchone()


@app.get("/api/documents")
def list_documents():
    return jsonify(documents=_doc_rows(get_db(), current_user(), request.args))


def load_doc(db, user, doc_id):
    d = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d or d["unit_id"] not in accessible_units(db, user):
        abort(404)
    return dict(d)


def sniff_ok(ext, head):
    sig = {"pdf": [b"%PDF"], "png": [b"\x89PNG"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
           "gif": [b"GIF8"], "webp": [b"RIFF"], "docx": [b"PK"], "xlsx": [b"PK"], "pptx": [b"PK"],
           "doc": [b"\xd0\xcf\x11\xe0"], "xls": [b"\xd0\xcf\x11\xe0"], "ppt": [b"\xd0\xcf\x11\xe0"]}
    return True if ext not in sig else any(head.startswith(s) for s in sig[ext])


def read_uploads():
    """Validate everything first, return [(orig_name, ext, bytes)]; raises ValueError."""
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        raise ValueError("Attach at least one file.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise ValueError(f"You can upload at most {MAX_FILES_PER_UPLOAD} files at a time.")
    out = []
    for f in files:
        name = os.path.basename(f.filename.replace("\\", "/"))[:150]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXT:
            raise ValueError(f"“{name}”: this file type is not allowed.")
        data = f.read()
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"“{name}” is larger than {MAX_FILE_MB} MB.")
        if not data:
            raise ValueError(f"“{name}” is empty.")
        if not sniff_ok(ext, data[:8]):
            raise ValueError(f"“{name}” does not look like a valid .{ext} file.")
        out.append((name, ext, data))
    return out


def save_files(db, doc_id, user, items):
    for name, ext, data in items:
        stored = secrets.token_hex(16) + "." + ext
        with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
            fh.write(data)
        db.execute("INSERT INTO files(doc_id,orig_name,stored_name,mime,size,uploaded_by,uploaded_at)"
                   " VALUES(?,?,?,?,?,?,?)",
                   (doc_id, name, stored, None, len(data), user["id"], now()))


@app.post("/api/documents")
def create_document():
    db, u, f = get_db(), current_user(), request.form
    try:
        unit_id = int(f.get("unit_id", 0))
    except ValueError:
        return err("Choose a section.")
    if not can_upload(db, u, unit_id):
        return err("You cannot upload to this section.", 403)
    unit = db.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
    title, year = f.get("title", "").strip(), f.get("academic_year", "")
    dtype, ddate = f.get("doc_type", ""), f.get("doc_date", "")
    quarter = f.get("quarter", "").strip() or None
    if not title or len(title) > 160:
        return err("Enter a title (maximum 160 characters).")
    if year not in academic_years():
        return err("Choose a valid academic year.")
    if dtype not in DOC_TYPES:
        return err("Choose a record type.")
    try:
        datetime.strptime(ddate, "%Y-%m-%d")
    except ValueError:
        return err("Enter a valid document / activity date.")
    if unit["quarterly"] and quarter not in ("Q1", "Q2", "Q3", "Q4"):
        return err("Select the quarter (Q1–Q4) for this section.")
    if not unit["quarterly"]:
        quarter = None
    try:
        items = read_uploads()
    except ValueError as e:
        return err(str(e))
    t = now()
    cur = db.execute("INSERT INTO documents(unit_id,academic_year,quarter,doc_date,title,doc_type,"
                     "submitted_by,notes,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (unit_id, year, quarter, ddate, title, dtype, f.get("submitted_by", "").strip()[:100],
                      f.get("notes", "").strip()[:1200], u["id"], t, t))
    save_files(db, cur.lastrowid, u, items)
    db.commit()
    audit("upload", f"doc#{cur.lastrowid} {title} ({len(items)} file(s)) in unit#{unit_id}")
    return jsonify(id=cur.lastrowid)


@app.get("/api/documents/<int:doc_id>")
def document_detail(doc_id):
    db, u = get_db(), current_user()
    d = load_doc(db, u, doc_id)
    by_id, _ = unit_tree(db)
    d["unit_path"] = unit_path(by_id, d["unit_id"])
    d["owner_name"] = db.execute("SELECT name FROM users WHERE id=?", (d["owner_id"],)).fetchone()[0]
    d["files"] = [dict(r) for r in db.execute(
        "SELECT id,orig_name,size,uploaded_at FROM files WHERE doc_id=? ORDER BY id", (doc_id,))]
    for fl in d["files"]:
        fl["previewable"] = fl["orig_name"].rsplit(".", 1)[-1].lower() in ("pdf", "jpg", "jpeg", "png", "gif", "webp")
    d["comments"] = [dict(r) for r in db.execute(
        "SELECT c.id,c.kind,c.new_status,c.body,c.is_admin,c.created_at,c.seen_by_dept,u.name AS author "
        "FROM comments c JOIN users u ON u.id=c.user_id WHERE c.doc_id=? ORDER BY c.id", (doc_id,))]
    for c in d["comments"]:
        c["unseen"] = bool(c["is_admin"] and not c["seen_by_dept"] and u["role"] != "admin")
    d["is_new"] = bool(u["role"] == "admin" and not d["admin_seen"])
    d["can_delete"] = can_delete(u, d)
    d["can_add_files"] = u["role"] == "member" and d["status"] != "approved"
    if u["role"] == "admin":
        db.execute("UPDATE documents SET admin_seen=1 WHERE id=?", (doc_id,))
    else:
        db.execute("UPDATE comments SET seen_by_dept=1 WHERE doc_id=? AND is_admin=1", (doc_id,))
    db.commit()
    return jsonify(document=d)


@app.post("/api/documents/<int:doc_id>/comments")
def add_comment(doc_id):
    db, u = get_db(), current_user()
    d = load_doc(db, u, doc_id)
    body = str((request.get_json(silent=True) or {}).get("body", "")).strip()
    if not body or len(body) > 2000:
        return err("Write a comment (maximum 2000 characters).")
    is_admin = 1 if u["role"] == "admin" else 0
    db.execute("INSERT INTO comments(doc_id,user_id,is_admin,body,seen_by_dept,created_at) VALUES(?,?,?,?,?,?)",
               (doc_id, u["id"], is_admin, body, 0 if is_admin else 1, now()))
    if not is_admin:
        db.execute("UPDATE documents SET admin_seen=0, updated_at=? WHERE id=?", (now(), doc_id))
    db.commit()
    audit("comment", f"doc#{doc_id}")
    return jsonify(ok=True)


@app.post("/api/documents/<int:doc_id>/status")
@admin_only
def set_status(doc_id):
    db, u = get_db(), current_user()
    load_doc(db, u, doc_id)
    d = request.get_json(silent=True) or {}
    st, note = d.get("status"), str(d.get("note", "")).strip()[:2000]
    if st not in STATUS_LABEL:
        return err("Invalid status.")
    if st == "revision" and not note:
        return err("Please add a comment explaining what needs to change.")
    db.execute("UPDATE documents SET status=?, updated_at=? WHERE id=?", (st, now(), doc_id))
    db.execute("INSERT INTO comments(doc_id,user_id,is_admin,kind,new_status,body,seen_by_dept,created_at)"
               " VALUES(?,?,?,?,?,?,0,?)", (doc_id, u["id"], 1, "status", st, note, now()))
    db.commit()
    audit("status", f"doc#{doc_id} -> {st}")
    return jsonify(ok=True)


@app.post("/api/documents/<int:doc_id>/files")
def add_files(doc_id):
    db, u = get_db(), current_user()
    d = load_doc(db, u, doc_id)
    if u["role"] != "member":
        return err("Only the submitting section can add files.", 403)
    if d["status"] == "approved":
        return err("This document has been approved and is locked.", 403)
    try:
        items = read_uploads()
    except ValueError as e:
        return err(str(e))
    save_files(db, doc_id, u, items)
    db.execute("UPDATE documents SET admin_seen=0, updated_at=?, status='submitted' WHERE id=?", (now(), doc_id))
    if d["status"] == "revision":
        db.execute("INSERT INTO comments(doc_id,user_id,is_admin,kind,new_status,body,seen_by_dept,created_at)"
                   " VALUES(?,?,0,'status','submitted',?,1,?)",
                   (doc_id, u["id"], f"Revised file(s) uploaded: {', '.join(i[0] for i in items)}", now()))
    db.commit()
    audit("add_files", f"doc#{doc_id} +{len(items)}")
    return jsonify(ok=True)


@app.delete("/api/documents/<int:doc_id>")
def delete_document(doc_id):
    db, u = get_db(), current_user()
    d = load_doc(db, u, doc_id)
    if not can_delete(u, d):
        return err("This document can no longer be deleted (it has been reviewed or belongs to another user).", 403)
    stored = [r[0] for r in db.execute("SELECT stored_name FROM files WHERE doc_id=?", (doc_id,))]
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    db.commit()
    for s in stored:
        try:
            os.remove(os.path.join(UPLOAD_DIR, s))
        except OSError:
            pass
    audit("delete", f"doc#{doc_id} {d['title']}")
    return jsonify(ok=True)


def send_stored(stored, orig_name):
    ext = orig_name.rsplit(".", 1)[-1].lower()
    inline = request.args.get("view") == "1" and ext in ("pdf", "jpg", "jpeg", "png", "gif", "webp")
    resp = send_file(os.path.join(UPLOAD_DIR, stored), as_attachment=not inline, download_name=orig_name)
    if inline and ext != "pdf":
        resp.headers["Content-Security-Policy"] = "sandbox"
    return resp


@app.get("/api/files/<int:file_id>")
def get_file(file_id):
    db, u = get_db(), current_user()
    fl = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not fl:
        abort(404)
    load_doc(db, u, fl["doc_id"])                      # enforces access
    return send_stored(fl["stored_name"], fl["orig_name"])

# ------------------------------------------------ Principal's documents (notices)
def notice_targets(db):
    t = {}
    for r in db.execute("SELECT notice_id, unit_id FROM notice_targets"):
        t.setdefault(r[0], []).append(r[1])
    return t


def notice_visible(user, n, targets, kids, acc):
    """Admin sees all. A member sees a notice sent to 'all', or to any unit that is
    (or contains / sits inside) one of the sections the member can open."""
    if user["role"] == "admin" or n["all_units"]:
        return True
    return any(set(descendants(kids, t)) & acc for t in targets.get(n["id"], []))


def notice_view(db, n, by_id, targets, reads):
    tg = targets.get(n["id"], [])
    return {"id": n["id"], "title": n["title"], "notes": n["notes"], "doc_date": n["doc_date"],
            "academic_year": n["academic_year"], "created_at": n["created_at"], "author": n["author"],
            "all_units": bool(n["all_units"]),
            "audience": "All sections" if n["all_units"] else ", ".join(by_id[t]["name"] for t in tg if t in by_id),
            "unread": n["id"] not in reads,
            "files": [{"id": f["id"], "orig_name": f["orig_name"], "size": f["size"],
                       "previewable": f["orig_name"].rsplit(".", 1)[-1].lower() in ("pdf", "jpg", "jpeg", "png", "gif", "webp")}
                      for f in db.execute("SELECT * FROM notice_files WHERE notice_id=? ORDER BY id", (n["id"],))]}


def visible_notices(db, user):
    by_id, kids = unit_tree(db)
    acc = accessible_units(db, user)
    targets = notice_targets(db)
    reads = {r[0] for r in db.execute("SELECT notice_id FROM notice_reads WHERE user_id=?", (user["id"],))}
    rows = db.execute("SELECT n.*, u.name AS author FROM notices n JOIN users u ON u.id=n.created_by "
                      "ORDER BY n.id DESC").fetchall()
    return [(n, notice_view(db, n, by_id, targets, reads)) for n in rows if notice_visible(user, n, targets, kids, acc)]


@app.get("/api/notices")
def list_notices():
    db, u = get_db(), current_user()
    items = [v for _, v in visible_notices(db, u)]
    unread = 0 if u["role"] == "admin" else sum(1 for v in items if v["unread"])
    if request.args.get("count"):
        return jsonify(unread=unread)
    if u["role"] == "admin":
        for v in items:
            v["unread"] = False
    return jsonify(notices=items, unread=unread)


@app.post("/api/notices")
@admin_only
def create_notice():
    db, u, f = get_db(), current_user(), request.form
    title, year, ddate = f.get("title", "").strip(), f.get("academic_year", ""), f.get("doc_date", "")
    all_units = f.get("audience") == "all"
    try:
        unit_ids = {int(x) for x in f.getlist("unit_ids")}
    except ValueError:
        return err("Invalid section selection.")
    valid = {r[0] for r in db.execute("SELECT id FROM units")}
    unit_ids &= valid
    if not title or len(title) > 160:
        return err("Enter a title (maximum 160 characters).")
    if year not in academic_years():
        return err("Choose a valid academic year.")
    try:
        datetime.strptime(ddate, "%Y-%m-%d")
    except ValueError:
        return err("Enter a valid date.")
    if not all_units and not unit_ids:
        return err("Choose who can see this: 'All sections' or at least one section.")
    try:
        items = read_uploads()
    except ValueError as e:
        return err(str(e))
    cur = db.execute("INSERT INTO notices(title,notes,doc_date,academic_year,all_units,created_by,created_at)"
                     " VALUES(?,?,?,?,?,?,?)",
                     (title, f.get("notes", "").strip()[:1200], ddate, year, int(all_units), u["id"], now()))
    nid = cur.lastrowid
    if not all_units:
        for x in unit_ids:
            db.execute("INSERT INTO notice_targets VALUES(?,?)", (nid, x))
    for name, ext, data in items:
        stored = secrets.token_hex(16) + "." + ext
        with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
            fh.write(data)
        db.execute("INSERT INTO notice_files(notice_id,orig_name,stored_name,size,uploaded_at) VALUES(?,?,?,?,?)",
                   (nid, name, stored, len(data), now()))
    db.commit()
    audit("notice_created", f"notice#{nid} {title} -> {'ALL' if all_units else sorted(unit_ids)}")
    return jsonify(id=nid)


def load_notice(db, user, nid):
    n = db.execute("SELECT n.*, u.name AS author FROM notices n JOIN users u ON u.id=n.created_by WHERE n.id=?",
                   (nid,)).fetchone()
    if not n:
        abort(404)
    by_id, kids = unit_tree(db)
    if not notice_visible(user, n, notice_targets(db), kids, accessible_units(db, user)):
        abort(404)
    return n


@app.get("/api/notices/<int:nid>")
def notice_detail(nid):
    db, u = get_db(), current_user()
    n = load_notice(db, u, nid)
    by_id, kids = unit_tree(db)
    targets = notice_targets(db)
    reads = {r[0] for r in db.execute("SELECT notice_id FROM notice_reads WHERE user_id=?", (u["id"],))}
    out = notice_view(db, n, by_id, targets, reads)
    if u["role"] == "admin":
        out["unread"] = False
        read_at = {r[0]: r[1] for r in db.execute("SELECT user_id, read_at FROM notice_reads WHERE notice_id=?", (nid,))}
        rec = []
        for m in db.execute("SELECT * FROM users WHERE role='member' AND active=1 ORDER BY name"):
            m = dict(m)
            if notice_visible(m, n, targets, kids, accessible_units(db, m)):
                rec.append({"name": m["name"], "email": m["email"], "read_at": read_at.get(m["id"])})
        out["recipients"] = rec
    else:
        db.execute("INSERT OR IGNORE INTO notice_reads VALUES(?,?,?)", (nid, u["id"], now()))
        db.commit()
    return jsonify(notice=out)


@app.delete("/api/notices/<int:nid>")
@admin_only
def delete_notice(nid):
    db = get_db()
    n = db.execute("SELECT title FROM notices WHERE id=?", (nid,)).fetchone()
    if not n:
        abort(404)
    stored = [r[0] for r in db.execute("SELECT stored_name FROM notice_files WHERE notice_id=?", (nid,))]
    db.execute("DELETE FROM notices WHERE id=?", (nid,))
    db.commit()
    for x in stored:
        try:
            os.remove(os.path.join(UPLOAD_DIR, x))
        except OSError:
            pass
    audit("notice_deleted", f"notice#{nid} {n['title']}")
    return jsonify(ok=True)


@app.get("/api/notice-files/<int:file_id>")
def get_notice_file(file_id):
    db, u = get_db(), current_user()
    fl = db.execute("SELECT * FROM notice_files WHERE id=?", (file_id,)).fetchone()
    if not fl:
        abort(404)
    load_notice(db, u, fl["notice_id"])                # enforces who-can-see
    return send_stored(fl["stored_name"], fl["orig_name"])

# ----------------------------------------------------------------------- stats
@app.get("/api/stats")
def stats():
    db, u = get_db(), current_user()
    rows = _doc_rows(db, u, {}, limit=100000)
    return jsonify(total=len(rows),
                   files=sum(r["file_count"] for r in rows),
                   today=sum(1 for r in rows if r["created_at"][:10] == today()),
                   pending=sum(1 for r in rows if r["status"] == "submitted"),
                   revision=sum(1 for r in rows if r["status"] == "revision"),
                   approved=sum(1 for r in rows if r["status"] == "approved"),
                   new=sum(1 for r in rows if r["is_new"]))

# ----------------------------------------------------------------- admin: users
def user_row(db, r):
    d = {k: r[k] for k in ("id", "email", "name", "role", "active", "must_change", "last_login")}
    d["unit_ids"] = [x[0] for x in db.execute("SELECT unit_id FROM user_units WHERE user_id=?", (r["id"],))]
    return d


def set_user_units(db, uid, unit_ids):
    db.execute("DELETE FROM user_units WHERE user_id=?", (uid,))
    valid = {r[0] for r in db.execute("SELECT id FROM units")}
    for x in {int(i) for i in unit_ids or []}:
        if x in valid:
            db.execute("INSERT INTO user_units VALUES(?,?)", (uid, x))


@app.get("/api/admin/users")
@admin_only
def admin_users():
    db = get_db()
    return jsonify(users=[user_row(db, r) for r in db.execute("SELECT * FROM users ORDER BY role,name")])


@app.post("/api/admin/users")
@admin_only
def admin_create_user():
    db, d = get_db(), request.get_json(silent=True) or {}
    email, name = str(d.get("email", "")).strip().lower(), str(d.get("name", "")).strip()
    role = d.get("role", "member")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not name or role not in ("admin", "member"):
        return err("Enter a valid email, name and role.")
    if role == "member" and not d.get("unit_ids"):
        return err("Assign at least one section to this member.")
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        return err("A user with this email already exists.")
    pw = random_password()
    cur = db.execute("INSERT INTO users(email,name,role,password_hash,must_change,created_at) VALUES(?,?,?,?,1,?)",
                     (email, name[:100], role, generate_password_hash(pw), now()))
    set_user_units(db, cur.lastrowid, d.get("unit_ids") if role == "member" else [])
    db.commit()
    audit("user_created", f"{email} ({role})")
    return jsonify(id=cur.lastrowid, temp_password=pw)


@app.patch("/api/admin/users/<int:uid>")
@admin_only
def admin_update_user(uid):
    db, me_, d = get_db(), current_user(), request.get_json(silent=True) or {}
    r = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        abort(404)
    role = d.get("role", r["role"])
    active = int(bool(d.get("active", r["active"])))
    if uid == me_["id"] and (role != "admin" or not active):
        return err("You cannot remove your own administrator access.")
    if role == "member" and not d.get("unit_ids", [x[0] for x in db.execute(
            "SELECT unit_id FROM user_units WHERE user_id=?", (uid,))]):
        return err("Assign at least one section to this member.")
    db.execute("UPDATE users SET name=?, role=?, active=? WHERE id=?",
               (str(d.get("name", r["name"])).strip()[:100] or r["name"], role, active, uid))
    if "unit_ids" in d or role == "admin":
        set_user_units(db, uid, d.get("unit_ids") if role == "member" else [])
    db.commit()
    audit("user_updated", r["email"])
    return jsonify(ok=True)


@app.post("/api/admin/users/<int:uid>/reset-password")
@admin_only
def admin_reset_password(uid):
    db = get_db()
    r = db.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        abort(404)
    pw = random_password()
    db.execute("UPDATE users SET password_hash=?, must_change=1 WHERE id=?", (generate_password_hash(pw), uid))
    db.commit()
    audit("password_reset", r["email"])
    return jsonify(temp_password=pw)

# ----------------------------------------------------------------- admin: units
@app.get("/api/admin/units")
@admin_only
def admin_units():
    by_id, kids = unit_tree(get_db())
    out = []

    def walk(pid, depth):
        for i in kids.get(pid, []):
            r = by_id[i]
            out.append({**r, "depth": depth, "path": unit_path(by_id, i)})
            walk(i, depth + 1)
    walk(None, 0)
    return jsonify(units=out)


@app.post("/api/admin/units")
@admin_only
def admin_add_unit():
    db, d = get_db(), request.get_json(silent=True) or {}
    name, code = str(d.get("name", "")).strip()[:120], str(d.get("code", "")).strip()[:20]
    parent = d.get("parent_id") or None
    if not name:
        return err("Enter a name.")
    if parent and not db.execute("SELECT 1 FROM units WHERE id=?", (parent,)).fetchone():
        return err("Parent not found.")
    if parent and db.execute("SELECT 1 FROM documents WHERE unit_id=?", (parent,)).fetchone():
        return err("That parent already holds documents. Add the new unit elsewhere, or move the documents first.")
    cur = db.execute("INSERT INTO units(parent_id,code,name,quarterly) VALUES(?,?,?,?)",
                     (parent, code, name, int(bool(d.get("quarterly")))))
    db.commit()
    audit("unit_added", f"{code} {name}")
    return jsonify(id=cur.lastrowid)


@app.patch("/api/admin/units/<int:uid>")
@admin_only
def admin_edit_unit(uid):
    db, d = get_db(), request.get_json(silent=True) or {}
    r = db.execute("SELECT * FROM units WHERE id=?", (uid,)).fetchone()
    if not r:
        abort(404)
    db.execute("UPDATE units SET name=?, code=?, quarterly=?, active=? WHERE id=?",
               (str(d.get("name", r["name"])).strip()[:120] or r["name"], str(d.get("code", r["code"])).strip()[:20],
                int(bool(d.get("quarterly", r["quarterly"]))), int(bool(d.get("active", r["active"]))), uid))
    db.commit()
    audit("unit_updated", r["name"])
    return jsonify(ok=True)

# ---------------------------------------------------------- admin: audit/backup
@app.get("/api/admin/audit")
@admin_only
def admin_audit():
    rows = get_db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 300").fetchall()
    return jsonify(entries=[dict(r) for r in rows])


@app.get("/api/admin/backup")
@admin_only
def admin_backup():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    snap = tmp.name + ".db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(snap)
    src.backup(dst); dst.close(); src.close()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "portal.db")
        for n in os.listdir(UPLOAD_DIR):
            z.write(os.path.join(UPLOAD_DIR, n), f"uploads/{n}")
    os.remove(snap)

    @after_this_request
    def cleanup(resp):
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        return resp
    audit("backup")
    return send_file(tmp.name, as_attachment=True, download_name=f"vc_doc_portal_backup_{today()}.zip")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=False)
