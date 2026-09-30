# Environment, Docker and file formats

One `.env` at the root, both compose files, and the line-ending and encoding rules that a
PowerShell redirect will quietly break.

# 4a. File Format Rules

## Line Endings: LF everywhere

**Every file in this repository uses LF (`\n`).** Never CRLF, in any file type, on any platform.

Rules:

* Write new files with LF
* Preserve LF when editing, do not let an editor or tool convert a file to CRLF
* `.gitattributes` enforces this (`* text=auto eol=lf`); do not add per-file overrides

Windows gotchas that silently rewrite a whole file to CRLF, avoid them:

* **PowerShell `Out-File` / `Set-Content` / `>`**, these write CRLF and, in Windows PowerShell 5.1,
  also mis-decode UTF-8 on the way in. Never round-trip a file through PowerShell to edit it. Use the
  editing tools, or `python`/`sed` if scripting.
* **`sed -i` on a CRLF working-tree file** strips the CR and rewrites the file as LF, correct here, but
  it shows as a whole-file diff if the blob was CRLF. Check `git diff --numstat` after any bulk edit: a
  content change of a few lines that reports hundreds of changed lines is a line-ending rewrite, not your
  edit.

Verify before committing:

```bash
# any CRLF file under a path?
grep -rlU $'\r' documentation/ || echo "all LF"

# did a bulk edit rewrite whole files?
git diff --numstat
```

Note that `core.autocrlf=true` on a dev machine makes the *working tree* CRLF regardless; the
`.gitattributes` rule is what keeps the committed blobs LF. Prefer `core.autocrlf=false` locally so what
you see is what is stored.

## Encoding: UTF-8, no BOM

Docs and source contain non-ASCII characters (te reo Māori macrons, arrows, emoji status markers).
Preserve them. Mojibake such as `â€"` or `Ã©` means a tool decoded UTF-8 as ANSI, revert the file and
redo the edit with a UTF-8-safe tool rather than hand-repairing it.

---

# 5. Environment Rules

Environment configuration is centralized.

Rules:

* Root `.env` is shared by frontend and backend
* Do not create `frontend/.env.local`
* Do not commit `.env`
* Do not commit credentials
* All backend environment variables must be defined in `backend/app/config.py`

The application should fail fast when required configuration is missing.

## Docker trap: a bind-mount source that does not exist

`docker-compose.dev.yml` bind-mounts `./service-account.json` and points
`GOOGLE_SERVICE_ACCOUNT_JSON` at it, overriding the inline JSON in `.env`. If that file is
missing when the container is first created, Docker silently creates an empty **directory**
in its place and every Drive job then fails with "points to a file that does not exist",
while the upload request itself still returns 200. Write the credential from `.env` to that
path (it is gitignored), then recreate the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api
```

A restart is not enough: the mount is resolved at container creation.
