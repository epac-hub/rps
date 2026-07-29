import datetime as dt
import json
import os
import pathlib
import re
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


def env(name, default=""):
    return os.environ.get(name, default).strip()


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


def decoded_data(path):
    obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    data = obj.get("data", [])
    return json.loads(data) if isinstance(data, str) else data


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    session, xsrf = login()

    for path, filename in [
        ("/getVehiclesByUser", "getVehiclesByUser.json"),
        ("/getVehiclesCurrentLocations", "getVehiclesCurrentLocations.json"),
        ("/getDomainsByUser", "getDomainsByUser.json"),
    ]:
        text = post_json(session, xsrf, path, {}, timeout=45)
        (RAW_DIR / filename).write_text(text, encoding="utf-8")

    end = dt.datetime.now().replace(microsecond=0)
    lookback_days = int(env("SKYTRACKIT_LOOKBACK_DAYS", "5"))
    start = end - dt.timedelta(days=lookback_days)

    vehicles = decoded_data(RAW_DIR / "getVehiclesByUser.json")
    serials = [row["avlSerial"] for row in vehicles if row.get("avlSerial")]
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
    for report_type, filename in REPORTS.items():
        body = dict(body_base)
        body["report_type"] = report_type
        try:
            text = post_json(session, xsrf, "/admin/getReport", body, timeout=120)
            (REPORT_DIR / f"{filename}.json").write_text(text, encoding="utf-8")
        except Exception as exc:
            (REPORT_DIR / f"{filename}.error.txt").write_text(str(exc), encoding="utf-8")
        time.sleep(0.4)

    print(f"Fetched SkyTrackIt data for {len(serials)} vehicles over {lookback_days} days.")


if __name__ == "__main__":
    main()
