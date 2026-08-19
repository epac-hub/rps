import datetime as dt
import json
import os
import pathlib
import re
import sys
import time

import requests


ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "skytrackit_raw"
REPORT_DIR = RAW_DIR / "reports"
BASE_URL = "https://v2.skytrackit.com"

REPORTS = {
    "1": "all_events_2days",
    "2": "ignition_2days",
    "3": "start_stop_2days",
    "4": "stop_events_2days",
    "5": "domain_activity_2days",
    "6": "current_locations_2days",
    "7": "speed_2days",
    "geofence": "geofence_2days",
}

# Files scripts/build_dashboard.py cannot run without. If any of these is
# missing the job must fail here, in the fetch step, instead of surfacing as a
# FileNotFoundError three steps later.
REQUIRED_ENDPOINTS = {"getVehiclesByUser.json", "getVehiclesCurrentLocations.json"}
REQUIRED_REPORTS = {
    "all_events_2days",
    "start_stop_2days",
    "stop_events_2days",
    "speed_2days",
}


def env(name, default=""):
    return os.environ.get(name, default).strip()


def env_int(name, default):
    try:
        return int(env(name, str(default)) or default)
    except ValueError:
        return default


ATTEMPTS = max(1, env_int("SKYTRACKIT_RETRIES", 3))
BACKOFF_SECONDS = max(1, env_int("SKYTRACKIT_BACKOFF_SECONDS", 5))
DEADLINE = time.monotonic() + max(60, env_int("SKYTRACKIT_MAX_SECONDS", 480))


def time_left():
    return DEADLINE - time.monotonic()


def login():
    username = env("SKYTRACKIT_USER")
    password = env("SKYTRACKIT_PASSWORD")
    if not username or not password:
        raise RuntimeError("Missing SKYTRACKIT_USER or SKYTRACKIT_PASSWORD.")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/html, */*",
    })
    login_url = f"{BASE_URL}/login"
    page = session.get(login_url, timeout=30)
    page.raise_for_status()
    match = re.search(r'name="_xsrf" value="([^"]+)"', page.text)
    payload = {"username": username, "password": password}
    if match:
        payload["_xsrf"] = match.group(1)

    response = session.post(
        login_url,
        data=payload,
        headers={"Referer": login_url, "Origin": BASE_URL},
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()
    if "Password Mismatch" in response.text or "Can't Login" in response.text:
        raise RuntimeError("SkyTrackIt login failed.")
    return session, session.cookies.get("_xsrf") or payload.get("_xsrf", "")


def post_json(session, xsrf, path, body, timeout=90):
    response = session.post(
        f"{BASE_URL}{path}",
        json=body,
        headers={
            "X-CSRFToken": xsrf,
            "Content-Type": "application/json",
            "Referer": f"{BASE_URL}/admin",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def validate_payload(text):
    """Reject anything that is not a usable SkyTrackIt JSON envelope.

    A expired session returns the HTML login page with HTTP 200, which used to
    be written to disk as a valid-looking report and then blew up downstream.
    """
    if not text or not text.strip():
        raise ValueError("empty response body")
    stripped = text.lstrip()
    if stripped[:1] not in "{[":
        raise ValueError(f"non-JSON response (starts with {stripped[:40]!r})")
    obj = json.loads(text)
    if isinstance(obj, dict) and "data" not in obj:
        raise ValueError(f"JSON envelope without 'data' key (keys: {sorted(obj)[:6]})")
    return obj


def write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def fetch_with_retries(session, xsrf, path, body, destination, timeout, label):
    """Return True on success. Retries transient failures within the deadline."""
    error_marker = destination.with_suffix(".error.txt")
    last_error = None
    for attempt in range(1, ATTEMPTS + 1):
        if time_left() <= 5:
            last_error = f"time budget exhausted before attempt {attempt}"
            break
        try:
            text = post_json(
                session, xsrf, path, body,
                timeout=min(timeout, max(15, int(time_left()))),
            )
            validate_payload(text)
            write_atomic(destination, text)
            error_marker.unlink(missing_ok=True)
            if attempt > 1:
                print(f"  {label}: recovered on attempt {attempt}.")
            return True
        except Exception as exc:  # noqa: BLE001 - reported and retried below
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  {label}: attempt {attempt}/{ATTEMPTS} failed - {last_error}")
            if attempt < ATTEMPTS:
                delay = min(BACKOFF_SECONDS * attempt, max(0, time_left() - 5))
                if delay > 0:
                    time.sleep(delay)

    error_marker.write_text(str(last_error), encoding="utf-8")
    return False


def decoded_data(path):
    obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    data = obj.get("data", [])
    return json.loads(data) if isinstance(data, str) else data


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    session, xsrf = login()

    failures = []

    for path, filename in [
        ("/getVehiclesByUser", "getVehiclesByUser.json"),
        ("/getVehiclesCurrentLocations", "getVehiclesCurrentLocations.json"),
        ("/getDomainsByUser", "getDomainsByUser.json"),
    ]:
        ok = fetch_with_retries(
            session, xsrf, path, {}, RAW_DIR / filename, 45, filename,
        )
        if not ok and filename in REQUIRED_ENDPOINTS:
            failures.append(filename)

    if failures:
        print(f"FATAL: required endpoint(s) unavailable: {', '.join(failures)}", file=sys.stderr)
        return 1

    end = dt.datetime.now().replace(microsecond=0)
    lookback_days = env_int("SKYTRACKIT_LOOKBACK_DAYS", 5)
    start = end - dt.timedelta(days=lookback_days)

    vehicles = decoded_data(RAW_DIR / "getVehiclesByUser.json")
    serials = [row["avlSerial"] for row in vehicles if row.get("avlSerial")]
    if not serials:
        print("FATAL: getVehiclesByUser returned no vehicles with avlSerial.", file=sys.stderr)
        return 1
    domains = sorted({row.get("domain") for row in vehicles if row.get("domain")}) or ["rpsmedicalcorp"]

    body_base = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "vehicles": serials,
        "key": "",
        "skip": 300,
        "domains": domains,
        "max_speed": 0,
        "temperatures": {"min": 30, "max": 70},
    }

    # Heaviest reports first so the required ones get the time budget while
    # there is still budget left.
    ordered = sorted(
        REPORTS.items(),
        key=lambda item: item[1] not in REQUIRED_REPORTS,
    )
    optional_failures = []
    for report_type, filename in ordered:
        body = dict(body_base)
        body["report_type"] = report_type
        ok = fetch_with_retries(
            session, xsrf, "/admin/getReport", body,
            REPORT_DIR / f"{filename}.json", 120, filename,
        )
        if not ok:
            (optional_failures if filename not in REQUIRED_REPORTS else failures).append(filename)
        time.sleep(0.4)

    if optional_failures:
        print(f"WARNING: optional report(s) unavailable: {', '.join(optional_failures)}")

    if failures:
        print(
            "FATAL: required report(s) unavailable after "
            f"{ATTEMPTS} attempts: {', '.join(failures)}. "
            "SkyTrackIt did not return the data; not rebuilding the dashboard "
            "so the last good version stays published.",
            file=sys.stderr,
        )
        return 1

    print(f"Fetched SkyTrackIt data for {len(serials)} vehicles over {lookback_days} days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
