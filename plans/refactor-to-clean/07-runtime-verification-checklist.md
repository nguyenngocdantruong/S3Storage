# Runtime Verification Checklist

Date: 2026-07-01
Branch: refactor/refactor-to-clean-architecture

This checklist is for the verification items that cannot be fully proven from the current local Codex environment.

## 1. Docker Build Verification

Run:

```powershell
docker compose up --build
```

Expected evidence:
- Image builds successfully.
- Container starts without Python import errors.
- App listens on port `7090`.
- Entrypoint remains `gunicorn app:app`.

If `docker compose` is unavailable, fallback:

```powershell
docker build -t video-s3-player .
docker run --rm -p 7090:7090 video-s3-player
```

## 2. Basic HTTP Smoke

After the app is up, verify:
- `GET /login` returns `200`
- `GET /register` returns `200`
- `GET /` redirects to `/login` or renders the dashboard depending on auth state
- `GET /search` redirects to `/login` when not authenticated

## 3. Authentication Flow

Verify manually:
- Login with the configured admin account from `config.conf`
- Logout works
- Profile page loads after login

Expected evidence:
- No template errors
- No route-not-found errors
- Session persists with `.secret_key`

## 4. Connection and Bucket Flow

Verify manually:
- Add an S3 connection
- Edit the connection
- Open the connection detail page
- Browse bucket list
- Create a bucket
- Delete a test bucket

Expected evidence:
- No legacy endpoint errors in redirects or templates
- Bucket listing works through the refactored storage provider

## 5. File Flow

Verify manually in a test bucket:
- Presigned upload path works
- Multipart upload path works
- Create folder works
- Rename file/folder works
- Delete single file works
- Delete multiple files works
- Download zip works
- Save text file works

Expected evidence:
- Upload quota logic still works
- Audit log entries are still created
- No broken JS fetch URLs in `browser.html`

## 6. Viewer / Media Flow

Verify manually:
- Open a normal file in viewer
- Proxy file route works
- Office file converts to PDF
- FLV converts to MP4
- HLS playlist loads
- HLS segment route returns media data

Expected evidence:
- ffmpeg/libreoffice wrappers are wired correctly after refactor
- No subprocess logic remains in interface modules

## 7. Progress / Notes / Likes

Verify manually:
- Video progress updates
- Progress list page loads
- Delete progress item works
- Delete progress bucket works
- Video notes list/create works
- Like API works

## 8. Admin / API Flow

Verify manually:
- Users page loads
- Update quota works
- Toggle status works
- Update role works
- Bucket access grant/revoke works
- Logs page loads
- System logs page loads as an audit table/list
- Login, upload, copy, and move actions appear in the admin audit table
- System log clear removes audit rows from the database
- Share APIs and paste APIs respond correctly

## Unit Test Suite

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

The suite is grouped under `tests/` by layer:
- `tests/application/`
- `tests/infrastructure/storage/`
- `tests/interfaces/admin/`
- `tests/interfaces/api/`
- `tests/interfaces/auth/`
- `tests/interfaces/viewer/`
- `tests/smoke/`
- `tests/use_cases/`

## 9. Final Architecture Invariants

Re-run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
rg -n "^import boto3$|^from boto3" -g "*.py" .
rg -n "^import subprocess$|^from subprocess" -g "*.py" .
```

Expected evidence:
- `total_routes=61`
- `failing_routes=0`
- `boto3` only in `infrastructure/storage/boto3_provider.py`
- `subprocess` only in `infrastructure/media/ffmpeg.py` and `infrastructure/media/libreoffice.py`
