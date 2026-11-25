# Tennis slot watcher

Checks the clubs in `config.toml` every 30 minutes and emails you when a court slot
becomes free that wasn't free at the previous check.

## Change what's watched
Edit `config.toml` (directly on GitHub is fine):
- `days_ahead` – how many days ahead to look, per club (Treptower Teufel: 5).
- `weekday_times` / `weekend_times` – e.g. `["17:00-22:00"]`; empty = all day.
- `courts` – e.g. `["Platz 11", "Platz 12", "Platz 13"]`; empty = all courts.
- `include_all_free` – also list every free slot in the email, not only new ones.

## One-time setup: push notifications (simplest)
1. Install the **ntfy** app (iPhone App Store / Google Play).
2. In the app tap **+**, enter your topic name (e.g. `tennis-` plus random letters, like a password), subscribe.
3. GitHub repo → Settings → Secrets and variables → Actions → New repository secret:
   `NTFY_TOPIC` = the same topic name.
4. Actions tab → "Check tennis slots" → **Run workflow** to test.

Keep the topic name private — anyone who knows it can read the notifications.
The first run lists the current free slots; after that, only new ones.

## Optional: email instead of / in addition to push
Add secrets `SMTP_USER` (Gmail address), `SMTP_PASSWORD` (Gmail app password),
`EMAIL_TO` (recipient).

## Run locally
    python3 check_slots.py --dry-run
