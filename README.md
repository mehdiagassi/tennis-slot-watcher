# Tennis slot watcher

Checks the clubs in `config.json` every 30 minutes and sends a push notification
(ntfy app) when a court slot opens up that wasn't free at the previous check.

## Phone app: Court Watch (`docs/`)
A home-screen web app to see free slots and change all settings.

1. Host it: repo **Settings → Pages** → Source "Deploy from a branch", branch `main`, folder `/docs`.
   (GitHub Pages needs the repo to be public, or a paid GitHub plan.)
2. On the iPhone, open the Pages link in **Safari** → Share → **Add to Home Screen**.
3. Create a fine-grained token at https://github.com/settings/personal-access-tokens/new:
   only this repository; permissions **Contents: Read and write**, **Actions: Read and write**.
4. Open Court Watch, paste the token, Connect. The token is stored on that phone only.

## Notifications (ntfy)
1. Install the **ntfy** app, tap **+**, subscribe to a private topic name (like a password, e.g. `tennis-` + random letters).
2. Repo → Settings → Secrets and variables → Actions → new secret `NTFY_TOPIC` = that name.

Optional email instead/as well: secrets `SMTP_USER` (Gmail), `SMTP_PASSWORD` (Gmail app password), `EMAIL_TO`.

## Files
- `config.json` – settings (the app edits this): days ahead, courts, time ranges per club, pause.
- `slots.json` – free slots from the last check (the app shows this).
- `state.json` – slots already notified, so only new ones alert.

## Run locally
    python3 check_slots.py --dry-run
