# Vivekananda College DOC Portal (server version)

Departments / cells / IQAC / Finance / Office / Library upload documents. The Principal (admin) sees everything,
comments on each document, and marks it **Approved** or **Changes requested**. Comments appear in the
uploading department's own account (red badge on the section + highlighted comment).

## How it works
| Who | Can do |
|---|---|
| **Member** (one login per department/section) | Open and upload only to the sections assigned to them; read the Principal's remarks, reply, upload revised files; delete own document **only until it is reviewed**; reports for own sections |
| **Principal / Admin** | See all sections and documents; comment; Approve / Request changes / Mark pending; reports for everything; create users, assign sections, reset passwords, add sections (e.g. missing Science departments); activity log; full backup |

All rules are enforced on the **server** (`app.py`). Files are only served to logged-in users who may see that section.

## Run on your computer
```bash
pip install -r requirements.txt
python app.py                     # http://127.0.0.1:8000
```
First run prints the **admin email + temporary password** in the terminal (or set `ADMIN_EMAIL` / `ADMIN_PASSWORD` first).
You must change it at first login.

Create sample logins for testing (one per main section, random passwords printed once):
```bash
python manage.py demo-users yourcollege.edu
python manage.py reset-password someone@yourcollege.edu     # emergency reset
```
For real use, create accounts in **Administration → Users & access** instead.

## Put it live (any host that runs Python, e.g. Render, Railway, PythonAnywhere, a VPS)
1. Upload this folder (it contains `Procfile`; start command = `gunicorn app:app`).
2. Set environment variables (see `.env.example`): `SECRET_KEY`, `COOKIE_SECURE=1`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `DATA_DIR`.
3. **`DATA_DIR` must be on a persistent disk/volume.** Free tiers that reset their disk on every restart will delete the database and all uploads.
4. Serve over **HTTPS** only.
5. Sign in as admin, change the password, then create the department logins.

## Before sharing with the college
- Add the missing Science departments: Administration → Sections (the college document has "……" between Computer Science and Zoology).
- Create the real accounts and send each temporary password privately; users are forced to change it.
- Download a backup (Administration → Backup) regularly. Test one restore.
- Don't put confidential student / exam / finance records in until the college is happy with hosting, backup and who has admin access.

## Files
`app.py` backend · `static/` interface · `manage.py` helper commands · `Procfile`, `requirements.txt`, `.env.example` deployment
