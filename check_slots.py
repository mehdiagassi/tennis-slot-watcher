#!/usr/bin/env python3
"""Check tennis clubs for free slots and notify (ntfy push and/or email) when new ones appear.

Settings: config.json (edited by the phone app in docs/ or by hand).
Outputs:  state.json (slots already reported) and slots.json (what the app shows).
"""
import argparse, json, os, re, smtplib, ssl, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta
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
def ebusy_booking_url(club, day=None):
    url = f"{club['base_url'].rstrip('/')}/lite-module/{club.get('module', 1)}"
    return url + (f"?selectedDate={day:%m/%d/%Y}" if day else "")


def ebusy_courts(club):
    """{court_id: {"name": "Platz 1", "info": "Traglufthalle (Sandplatz)"}}"""
    html = http_get(ebusy_booking_url(club))
    courts = {}
    for cid, inner in re.findall(r'<th class="court[^"]*" data-id="(\d+)"[^>]*>(.*?)</th>', html, re.S):
        parts = [p.strip() for p in re.split(r"<[^>]+>", inner) if p.strip()]
        name = parts[0] if parts else f"Court {cid}"
        info = " ".join(p for p in parts[1:] if not re.fullmatch(r"P\d+", p))
        courts[int(cid)] = {"name": name, "info": info}
    if not courts:
        raise RuntimeError("could not read the court list (did the booking page change?)")
    return courts


def free_starts(busy, start, end, step, grid=30):
    """Start times (minutes) of free slots between bookings.

    Each gap between bookings is filled from its start in `step`-long slots, so a
    free hour that begins at :30 (e.g. 16:30-17:30) is found too. Gap starts are
    rounded up to the booking grid (`grid` minutes).
    """
    t = start
    out = []
    for s, e in sorted(busy) + [(end, end)]:
        gap_end = min(s, end)
        t = -(-t // grid) * grid  # round up to the grid
        while t + step <= gap_end:
            out.append(t)
            t += step
        t = max(t, e)
        if t >= end:
            break
    return out


def ebusy_free_slots(club, now):
    courts = ebusy_courts(club)
    today = now.date()
    days = [today + timedelta(days=i) for i in range(int(club.get("days_ahead", 5)) + 1)]
    busy, fetched = {}, set()  # (date, court) -> [(start, end)] in minutes
    for d in days:
        if d in fetched:
            continue
        q = urllib.parse.urlencode({"timestamp": "", "selectedDate": d.strftime("%m/%d/%Y")})
        data = http_get(f"{ebusy_booking_url(club)}?{q}", json_mode=True)
        if "reservations" not in data:
            raise RuntimeError("unexpected answer from the booking system")
        for r in data["reservations"]:
            rd = datetime.strptime(r["date"], "%m/%d/%Y").date()
            fetched.add(rd)
            s, e = hm(r["fromTime"]), hm(r["toTime"])
            if e <= s:
                e += 24 * 60
            busy.setdefault((rd, r["court"]), []).append((s, e))
        fetched.add(d)

    step = int(club.get("slot_minutes", 60))
    start, end = hm(club.get("open_from", "06:00")), hm(club.get("open_until", "23:00"))
    if end <= start:
        end += 24 * 60
    slots = []
    for d in days:
        for cid, c in courts.items():
            for t in free_starts(busy.get((d, cid), []), start, end, step):
                begin = datetime.combine(d, datetime.min.time(), TZ) + timedelta(minutes=t)
                if begin <= now:
                    continue
                slots.append({"club": club["name"], "court": c["name"], "start": begin,
                              "minutes": step, "url": ebusy_booking_url(club, d)})
    return slots, [{"name": c["name"], "info": c["info"]} for c in courts.values()]


# ---------- GotCourts ----------
GOTCOURTS_API = "https://app-api.gotcourts.com/v1/prod"


def gotcourts_booking_url(club, day=None):
    return f"https://apps.gotcourts.com/newplayer/club/{club['club_id']}"


def gotcourts_free_slots(club, now):
    sport = club.get("sport", "tennis")
    today = now.date()
    days = [today + timedelta(days=i) for i in range(int(club.get("days_ahead", 7)) + 1)]
    courts, slots = {}, []
    for d in days:
        day_ts = int(datetime.combine(d, datetime.min.time(), TZ).timestamp())
        q = urllib.parse.urlencode({"day": day_ts, "flows": "ncc,syc,cll"})
        data = http_get(f"{GOTCOURTS_API}/slots/club/{club['club_id']}?{q}", json_mode=True)
        if "data" not in data or "courts" not in data["data"]:
            raise RuntimeError(f"unexpected answer from GotCourts ({data.get('responseCode')})")
        for c in data["data"]["courts"]:
            if c.get("sport") != sport:
                continue
            name = re.sub(r"^(Tennis\s+)?(Indoor\s+|Outdoor\s+)?", "", c["name"]).strip() or c["name"]
            surface = (c.get("surfaceType") or "").replace("_", " ").title()
            kind = (c.get("courtType") or "").title()
            courts[c["courtId"]] = {"name": name, "info": f"{kind} ({surface})" if surface else kind}
            for sl in c.get("slots", []):
                if sl.get("slotType") != "available":
                    continue
                begin = datetime.fromtimestamp(sl["startDateTime"], TZ)
                end = datetime.fromtimestamp(sl["endDateTime"], TZ)
                if begin <= now:
                    continue
                slots.append({"club": club["name"], "court": name, "start": begin,
                              "minutes": int((end - begin).total_seconds() // 60),
                              "url": gotcourts_booking_url(club, d)})
    if not courts:
        raise RuntimeError(f"no {sport} courts found for this club")
    return slots, list(courts.values())


ADAPTERS = {"ebusy": ebusy_free_slots, "gotcourts": gotcourts_free_slots}
BOOKING_URLS = {"ebusy": ebusy_booking_url, "gotcourts": gotcourts_booking_url}


# ---------- filtering / formatting ----------
def in_windows(minute_of_day, windows):
    for w in windows:
        a, b = (hm(x) for x in w.split("-"))
        if (a <= minute_of_day < b) if a < b else (minute_of_day >= a or minute_of_day < b):
            return True
    return False


def apply_filters(club, slots):
    """Keep only the courts chosen for this club (empty list = all courts)."""
    courts = set(club.get("courts") or [])
    return [s for s in slots if not courts or s["court"] in courts]


def wants_alert(cfg, s):
    """Does this slot's start time fall in the alert times? No ranges = any time."""
    times = cfg.get("alert_times") or {}
    windows = times.get("weekends" if s["start"].weekday() >= 5 else "weekdays") or []
    return not windows or in_windows(s["start"].hour * 60 + s["start"].minute, windows)


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


# ---------- notifications ----------
def send_push(title, body, click_url=None):
    """Push notification via ntfy (https://ntfy.sh). Topic comes from NTFY_TOPIC."""
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    data = body.encode("utf-8")
    if len(data) > 3900:  # ntfy message limit is 4096 bytes
        data = data[:3850].decode("utf-8", "ignore").rsplit("\n", 1)[0].encode() + "\n… (more)".encode()
    headers = {"Title": title.encode("utf-8").decode("latin-1"), "Tags": "tennis", "Priority": "high"}
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(f"{server}/{urllib.parse.quote(os.environ['NTFY_TOPIC'])}",
                                 data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def send_email(subject, body, to):
    user, pw = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, os.environ.get("EMAIL_TO") or to or user, subject
    msg.set_content(body)
    host, port = os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)


def notify(cfg, new, current, errors):
    subject = f"{len(new)} new tennis slot(s) - " + ", ".join(sorted({s['club'] for s in new}))
    sent = False
    if os.environ.get("NTFY_TOPIC"):
        first = sorted(new, key=lambda s: s["start"])[0]
        send_push(subject, group_text(new), first["url"])
        sent = True
        print(f"pushed {len(new)} new slot(s)")
    if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
        parts = [f"{len(new)} new free slot(s):\n", group_text(new)]
        if cfg.get("email", {}).get("include_all_free", True):
            parts += [f"\n\nAll free slots in your window ({len(current)}):\n", group_text(current)]
        if errors:
            parts += ["\n\nProblems during this check:\n" + "\n".join(errors)]
        send_email(subject, "\n".join(parts), cfg.get("email", {}).get("to"))
        sent = True
        print(f"emailed {len(new)} new slot(s)")
    if not sent:
        raise RuntimeError("no notification channel set up: add the NTFY_TOPIC secret (or SMTP_USER/SMTP_PASSWORD)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--slots", default="slots.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of notifying; write nothing")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    now = datetime.now(TZ)
    state_path = Path(args.state)
    seen = set(json.loads(state_path.read_text())["free"]) if state_path.exists() else None

    current, errors, club_info = [], [], {}
    for club in cfg["clubs"]:
        if not club.get("enabled", True):
            continue
        try:
            slots, courts = ADAPTERS[club["type"]](club, now)
            current += apply_filters(club, slots)
            club_info[club["name"]] = {"courts": courts, "booking_url": BOOKING_URLS[club["type"]](club),
                                       "days_ahead": club.get("days_ahead")}
        except Exception as e:  # keep going with the other clubs
            errors.append(f"{club['name']}: {e}")
    if errors:
        print("ERRORS:\n" + "\n".join(errors), file=sys.stderr)

    cur_keys = {key(s) for s in current}
    new = [s for s in current if seen is None or key(s) not in seen]
    new_keys = {key(s) for s in new}
    alerts = [s for s in new if wants_alert(cfg, s)]
    paused = cfg.get("paused", False)

    if args.dry_run:
        print(f"{len(new)} new ({len(alerts)} in alert times), {len(current)} free in window\n" + group_text(alerts))
        return

    # Write what the app shows first, so it is up to date even if notifying fails
    Path(args.slots).write_text(json.dumps({
        "checked": now.isoformat(timespec="seconds"),
        "paused": paused,
        "errors": errors,
        "clubs": club_info,
        "free": [{"club": s["club"], "court": s["court"], "start": s["start"].isoformat(timespec="minutes"),
                  "minutes": s["minutes"], "url": s["url"], "new": key(s) in new_keys,
                  "alert": wants_alert(cfg, s)}
                 for s in sorted(current, key=lambda s: (s["start"], s["club"], s["court"]))],
    }, ensure_ascii=False, indent=1) + "\n")


    if alerts and not paused:
        notify(cfg, alerts, current, errors)
    elif paused:
        print("paused, no notifications")
    else:
        print(f"{len(new)} new slot(s), none in your alert times ({len(current)} free in window)")

    # Remember what was free, keeping old entries for clubs that failed this time
    failed = {e.split(":")[0] for e in errors}
    keep = {k for k in (seen or set()) if k.split("|")[0] in failed}
    state_path.write_text(json.dumps({"checked": now.isoformat(), "free": sorted(cur_keys | keep)}, indent=1) + "\n")

if __name__ == "__main__":
    main()
