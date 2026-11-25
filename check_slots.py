#!/usr/bin/env python3
"""Check tennis clubs for free slots and email when new ones appear."""
import argparse, json, os, re, smtplib, ssl, sys, tomllib, urllib.parse, urllib.request
from datetime import datetime, timedelta, date
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (tennis-slot-watcher; personal use)"
DE_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def http_get(url, json_mode=False):
    headers = {"User-Agent": UA}
    if json_mode:
        headers.update({"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if json_mode else body


def hm(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m


# ---------- eBuSy ----------
def ebusy_courts(club):
    html = http_get(f"{club['base_url']}/lite-module/{club['module']}")
    courts = {}
    for cid, title in re.findall(r'<th class="court[^"]*" data-id="(\d+)" title="([^"]+)"', html):
        courts[int(cid)] = title
    if not courts:
        raise RuntimeError("could not read court list from eBuSy page (layout changed?)")
    return courts


def ebusy_reservations(club, day):
    q = urllib.parse.urlencode({"timestamp": "", "selectedDate": day.strftime("%m/%d/%Y")})
    data = http_get(f"{club['base_url']}/lite-module/{club['module']}?{q}", json_mode=True)
    if "reservations" not in data:
        raise RuntimeError("unexpected eBuSy response (no 'reservations')")
    return data["reservations"]


def ebusy_free_slots(club, now):
    courts = ebusy_courts(club)
    today = now.date()
    days = [today + timedelta(days=i) for i in range(club.get("days_ahead", 5) + 1)]
    busy = {}  # (date, court) -> list of (start, end) minutes
    fetched = set()
    for d in days:
        if d in fetched:
            continue
        for r in ebusy_reservations(club, d):
            rd = datetime.strptime(r["date"], "%m/%d/%Y").date()
            fetched.add(rd)
            s, e = hm(r["fromTime"]), hm(r["toTime"])
            if e <= s:
                e += 24 * 60  # runs past midnight
            busy.setdefault((rd, r["court"]), []).append((s, e))
        fetched.add(d)

    step = club.get("slot_minutes", 60)
    start, end = hm(club.get("open_from", "06:00")), hm(club.get("open_until", "23:00"))
    if end <= start:
        end += 24 * 60
    slots = []
    for d in days:
        for cid, cname in courts.items():
            for t in range(start, end - step + 1, step):
                if any(s < t + step and t < e for s, e in busy.get((d, cid), [])):
                    continue
                begin = datetime.combine(d, datetime.min.time(), TZ) + timedelta(minutes=t)
                if begin <= now:
                    continue
                slots.append({"club": club["name"], "court": cname, "start": begin, "minutes": step})
    return slots


ADAPTERS = {"ebusy": ebusy_free_slots}


# ---------- filtering / formatting ----------
def in_windows(minute_of_day, windows):
    for w in windows:
        a, b = w.split("-")
        a, b = hm(a), hm(b)
        if (a <= minute_of_day < b) if a < b else (minute_of_day >= a or minute_of_day < b):
            return True
    return False


def apply_filters(club, slots):
    out = []
    courts = set(club.get("courts") or [])
    for s in slots:
        if courts and s["court"] not in courts:
            continue
        mod = s["start"].hour * 60 + s["start"].minute
        windows = club.get("weekend_times" if s["start"].weekday() >= 5 else "weekday_times") or []
        if windows and not in_windows(mod, windows):
            continue
        out.append(s)
    return out


def key(s):
    return f"{s['club']}|{s['court']}|{s['start'].isoformat()}"


def fmt(s):
    st = s["start"]
    en = st + timedelta(minutes=s["minutes"])
    return f"{DE_DAYS[st.weekday()]} {st:%d.%m.} {st:%H:%M}–{en:%H:%M}  {s['court']}"


def group_text(slots):
    lines, cur = [], None
    for s in sorted(slots, key=lambda s: (s["club"], s["start"], s["court"])):
        if s["club"] != cur:
            cur = s["club"]
            lines.append(f"\n== {cur} ==")
        lines.append("  " + fmt(s))
    return "\n".join(lines).strip()


def send_email(subject, body, to):
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not (user and pw):
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD not set")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, os.environ.get("EMAIL_TO") or to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)


def send_push(title, body, click_url=None):
    """Push notification via ntfy (https://ntfy.sh). Topic comes from NTFY_TOPIC."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("NTFY_TOPIC not set")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    data = body.encode("utf-8")
    if len(data) > 3900:  # ntfy message limit is 4096 bytes
        data = data[:3850].decode("utf-8", "ignore").rsplit("\n", 1)[0].encode() + "\n… (more)".encode()
    headers = {"Title": title.encode("utf-8").decode("latin-1"), "Tags": "tennis", "Priority": "high"}
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(f"{server}/{urllib.parse.quote(topic)}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of emailing; don't save state")
    args = ap.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text())
    now = datetime.now(TZ)
    state_path = Path(args.state)
    seen = set(json.loads(state_path.read_text())["free"]) if state_path.exists() else None

    current, errors = [], []
    for club in cfg["clubs"]:
        try:
            current += apply_filters(club, ADAPTERS[club["type"]](club, now))
        except Exception as e:  # keep going with other clubs
            errors.append(f"{club['name']}: {e}")

    cur_keys = {key(s) for s in current}
    new = [s for s in current if seen is None or key(s) not in seen]

    if errors:
        print("ERRORS:\n" + "\n".join(errors), file=sys.stderr)

    if new:
        parts = [f"{len(new)} new free slot(s):\n", group_text(new)]
        if cfg["email"].get("include_all_free", True):
            parts += [f"\n\nAll free slots in your window ({len(current)}):\n", group_text(current)]
        if errors:
            parts += ["\n\nProblems during this check:\n" + "\n".join(errors)]
        body = "\n".join(parts)
        subject = f"{len(new)} new tennis slot(s) - " + ", ".join(sorted({s['club'] for s in new}))
        if args.dry_run:
            print(subject + "\n\n" + body)
        else:
            sent = False
            if os.environ.get("NTFY_TOPIC"):
                clubs_new = [c for c in cfg["clubs"] if any(s["club"] == c["name"] for s in new)]
                click = f"{clubs_new[0]['base_url']}/lite-module/{clubs_new[0].get('module', 1)}" if clubs_new else None
                push_body = group_text(new)  # push: only the new slots, keeps it short
                send_push(subject, push_body, click)
                sent = True
                print(f"pushed {len(new)} new slot(s)")
            if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
                send_email(subject, body, cfg["email"]["to"])
                sent = True
                print(f"emailed {len(new)} new slot(s)")
            if not sent:
                raise RuntimeError("no notification channel configured: set NTFY_TOPIC (or SMTP_USER/SMTP_PASSWORD)")
    else:
        print(f"no new slots ({len(current)} free in window)")

    if not args.dry_run and not errors:
        state_path.write_text(json.dumps({"checked": now.isoformat(), "free": sorted(cur_keys)}, indent=1))
    elif not args.dry_run and errors:
        # keep old knowledge for failed clubs so they don't re-alert everything next time
        keep = {k for k in (seen or set()) if any(k.startswith(e.split(":")[0] + "|") for e in errors)}
        state_path.write_text(json.dumps({"checked": now.isoformat(), "free": sorted(cur_keys | keep)}, indent=1))


if __name__ == "__main__":
    main()
