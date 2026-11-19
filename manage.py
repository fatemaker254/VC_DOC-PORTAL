"""Command-line helpers.

  python manage.py demo-users [email-domain]   create one login per main section (random passwords, printed once)
  python manage.py reset-password <email>      set a new temporary password for any user
"""
import csv, sys
from werkzeug.security import generate_password_hash
import app as portal


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    db = portal.sqlite3.connect(portal.DB_PATH)
    db.row_factory = portal.sqlite3.Row
    if cmd == "demo-users":
        domain = sys.argv[2] if len(sys.argv) > 2 else "college.example"
        groups = {"iqac": "IQAC", "arts": "Arts", "commerce": "Commerce", "science": "Science",
                  "cells": "Cells / Committees", "office": "College Office", "finance": "Finance", "library": "Library"}
        out = []
        for slug, unit_name in groups.items():
            unit = db.execute("SELECT id FROM units WHERE name=? AND active=1", (unit_name,)).fetchone()
            email = f"{slug}@{domain}"
            if not unit or db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                continue
            pw = portal.random_password()
            cur = db.execute("INSERT INTO users(email,name,role,password_hash,must_change,created_at) VALUES(?,?,?,?,1,?)",
                             (email, f"{unit_name} Account", "member", generate_password_hash(pw), portal.now()))
            db.execute("INSERT INTO user_units VALUES(?,?)", (cur.lastrowid, unit["id"]))
            out.append((email, pw, unit_name))
        db.commit()
        with open("demo_credentials.csv", "w", newline="") as f:
            csv.writer(f).writerows([("email", "temporary_password", "section"), *out])
        for e, p, u in out:
            print(f"{e:35} {p:16} {u}")
        print("\nSaved to demo_credentials.csv  (delete this file after sharing the passwords privately)")
    elif cmd == "reset-password" and len(sys.argv) > 2:
        pw = portal.random_password()
        n = db.execute("UPDATE users SET password_hash=?, must_change=1, active=1 WHERE email=?",
                       (generate_password_hash(pw), sys.argv[2].lower())).rowcount
        db.commit()
        print(f"New temporary password: {pw}" if n else "No such user.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
