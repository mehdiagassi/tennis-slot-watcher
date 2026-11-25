# Tennis slot watcher

Checks the clubs in `config.toml` every 30 minutes and emails you when a court slot
becomes free that wasn't free at the previous check.

## Change what's watched
Edit `config.toml` (directly on GitHub is fine):
- `days_ahead` – how many days ahead to look, per club (Treptower Teufel: 5).
- `weekday_times` / `weekend_times` – e.g. `["17:00-22:00"]`; empty = all day.
- `courts` – e.g. `["Platz 11", "Platz 12", "Platz 13"]`; empty = all courts.
- `include_all_free` – also list every free slot in the email, not only new ones.

## One-time setup (GitHub → repo → Settings → Secrets and variables → Actions)
| Secret | Value |
|---|---|
| `SMTP_USER` | your Gmail address (sender) |
| `SMTP_PASSWORD` | a Gmail **app password** (Google Account → Security → 2-Step Verification → App passwords) |
| `EMAIL_TO` | address to receive alerts (can be the same Gmail) |

Then Actions tab → "Check tennis slots" → **Run workflow** to test.
The first run emails the full list of free slots; after that, only new ones.

## Run locally
    python3 check_slots.py --dry-run
