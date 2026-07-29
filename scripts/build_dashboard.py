import csv
import datetime as dt
import html
import json
import math
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "skytrackit_raw"
REPORTS = RAW / "reports"
DASH = ROOT
LOCAL_OFFSET_HOURS = -4
SPEED_LIMIT = 65


def parse_time(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00").split(".")[0])
    except ValueError:
        return None


def local_time(value):
    parsed = parse_time(value)
    return parsed + dt.timedelta(hours=LOCAL_OFFSET_HOURS) if parsed else None


def iso(value):
    return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else ""


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def flatten_report(filename):
    obj = load_json(REPORTS / filename)
    data = obj.get("data", [])
    if isinstance(data, str):
        data = json.loads(data)
    rows = []
    for group in data:
        if isinstance(group, list):
            rows.extend([row for row in group if isinstance(row, dict)])
        elif isinstance(group, dict):
            rows.append(group)
    rows.sort(key=lambda row: row.get("avlTime", ""))
    return rows


def load_list(filename):
    obj = load_json(RAW / filename)
    data = obj.get("data", [])
    return json.loads(data) if isinstance(data, str) else data


def haversine_miles(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]:
        return 0
    radius = 3958.7613
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def duration_text(seconds):
    seconds = max(0, int(seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_trips(start_stop, all_events):
    by_serial = {}
    for row in start_stop:
        by_serial.setdefault(row.get("avlSerial"), []).append(row)
    events_by_serial = {}
    for row in all_events:
        events_by_serial.setdefault(row.get("avlSerial"), []).append(row)
    trips = []
    for serial, rows in by_serial.items():
        rows.sort(key=lambda row: row.get("avlTime", ""))
        active = None
        for row in rows:
            reason = (row.get("reason") or "").upper()
            if reason == "IGNITION ON":
                active = row
            elif reason == "IGNITION OFF" and active:
                start = parse_time(active.get("avlTime"))
                end = parse_time(row.get("avlTime"))
                if not start or not end:
                    active = None
                    continue
                path_events = []
                for event in events_by_serial.get(serial, []):
                    event_time = parse_time(event.get("avlTime"))
                    if event_time and start <= event_time <= end and event.get("latitude") and event.get("longitude"):
                        path_events.append(event)
                sampled = path_events[:: max(1, len(path_events) // 80)] if path_events else []
                route_points = [[e.get("latitude"), e.get("longitude")] for e in sampled]
                straight = haversine_miles(active.get("latitude"), active.get("longitude"), row.get("latitude"), row.get("longitude"))
                odometer_miles = max(0, ((row.get("odometer") or 0) - (active.get("odometer") or 0)) / 1609.344)
                efficiency_ratio = round(odometer_miles / straight, 2) if straight > 0.2 and odometer_miles else None
                potential_saved = 0
                if efficiency_ratio is None:
                    efficiency_label = "Sin distancia suficiente"
                    efficiency_reason = "La salida y llegada estan demasiado cerca o falta odometro para evaluar eficiencia."
                    efficiency_action = "Validar GPS/odometro; esta ruta no tiene distancia suficiente para comparacion operacional."
                elif efficiency_ratio <= 1.35:
                    efficiency_label = "Eficiente"
                    efficiency_reason = f"Recorrio {efficiency_ratio}x la distancia directa; esta dentro del umbral operacional de 1.35x."
                    efficiency_action = "Mantener patron actual; usar como referencia para rutas similares."
                elif efficiency_ratio <= 1.8:
                    efficiency_label = "Revisar"
                    target_miles = straight * 1.35
                    potential_saved = max(0, odometer_miles - target_miles)
                    efficiency_reason = f"Recorrio {efficiency_ratio}x la distancia directa; supera el umbral eficiente de 1.35x."
                    efficiency_action = f"Para ser eficiente debe acercarse a {target_miles:.1f} mi o menos; posible ahorro {potential_saved:.1f} mi. Revisar trafico, secuencia de entregas y desvio autorizado."
                else:
                    efficiency_label = "Ineficiente"
                    target_miles = straight * 1.35
                    potential_saved = max(0, odometer_miles - target_miles)
                    efficiency_reason = f"Recorrio {efficiency_ratio}x la distancia directa; la ruta fue mucho mas larga que el trayecto esperado."
                    efficiency_action = f"Alternativa: reordenar paradas y comparar contra Google Routes antes del despacho. Para pasar a eficiente: {target_miles:.1f} mi o menos; ahorro estimado {potential_saved:.1f} mi."
                trips.append({
                    "vehicle": row.get("alias") or active.get("alias") or serial,
                    "serial": serial,
                    "departure": iso(local_time(active.get("avlTime"))),
                    "arrival": iso(local_time(row.get("avlTime"))),
                    "duration_min": round((end - start).total_seconds() / 60, 1),
                    "origin": active.get("geocode", ""),
                    "destination": row.get("geocode", ""),
                    "origin_lat": active.get("latitude"),
                    "origin_lng": active.get("longitude"),
                    "destination_lat": row.get("latitude"),
                    "destination_lng": row.get("longitude"),
                    "miles": round(odometer_miles, 2),
                    "straight_miles": round(straight, 2),
                    "efficiency_ratio": efficiency_ratio,
                    "efficiency": efficiency_label,
                    "why_inefficient": efficiency_reason,
                    "efficient_action": efficiency_action,
                    "potential_saved_miles": round(potential_saved, 2),
                    "route_points": route_points,
                })
                active = None
    trips.sort(key=lambda row: row["departure"])
    return trips


def build_stops(stops):
    rows = []
    for row in stops:
        rows.append({
            "vehicle": row.get("alias", ""),
            "serial": row.get("avlSerial", ""),
            "time": iso(local_time(row.get("avlTime"))),
            "place": row.get("geocode", ""),
            "speed": round(float(row.get("speed") or 0), 1),
            "lat": row.get("latitude"),
            "lng": row.get("longitude"),
        })
    return rows


def build_speeding(speed):
    rows = []
    seen = set()
    for row in speed:
        sp = float(row.get("speed") or 0)
        key = (row.get("avlSerial"), row.get("avlTime"), round(sp, 1))
        if sp <= SPEED_LIMIT or key in seen:
            continue
        seen.add(key)
        rows.append({
            "vehicle": row.get("alias", ""),
            "serial": row.get("avlSerial", ""),
            "time": iso(local_time(row.get("avlTime"))),
            "speed": round(sp, 1),
            "over": round(sp - SPEED_LIMIT, 1),
            "place": row.get("geocode", ""),
            "lat": row.get("latitude"),
            "lng": row.get("longitude"),
        })
    rows.sort(key=lambda row: row["speed"], reverse=True)
    return rows


def current_vehicles(current):
    rows = []
    for row in current:
        info = row.get("avlinfo", {})
        loc = row.get("location", {}).get("coordinates") or []
        lng = loc[0] if len(loc) > 1 else row.get("longitude")
        lat = loc[1] if len(loc) > 1 else row.get("latitude")
        rows.append({
            "vehicle": info.get("vehicleAlias") or row.get("vehicleAlias") or row.get("avlSerial"),
            "serial": row.get("avlSerial"),
            "plate": info.get("vehiclePlateid") or row.get("vehiclePlateid", ""),
            "description": info.get("description", ""),
            "time": iso(local_time(row.get("avlTime"))),
            "speed": round(float(info.get("speed") or 0), 1),
            "ignition": info.get("ignition", ""),
            "lat": lat,
            "lng": lng,
            "place": info.get("geocode") or row.get("geocode") or "",
        })
    rows.sort(key=lambda row: row["vehicle"])
    return rows


def main():
    DASH.mkdir(parents=True, exist_ok=True)
    vehicles = load_list("getVehiclesByUser.json")
    current = current_vehicles(load_list("getVehiclesCurrentLocations.json"))
    all_events = flatten_report("all_events_2days.json")
    trips = build_trips(flatten_report("start_stop_2days.json"), all_events)
    stops = build_stops(flatten_report("stop_events_2days.json"))
    speeding = build_speeding(flatten_report("speed_2days.json"))

    summary = []
    for vehicle in current:
        serial = vehicle["serial"]
        v_trips = [row for row in trips if row["serial"] == serial]
        v_stops = [row for row in stops if row["serial"] == serial]
        v_speed = [row for row in speeding if row["serial"] == serial]
        inefficient = [row for row in v_trips if row["efficiency"] == "Ineficiente"]
        summary.append({
            "vehicle": vehicle["vehicle"],
            "serial": serial,
            "plate": vehicle["plate"],
            "current_status": "Moviendo" if vehicle["speed"] > 2 else "Detenido",
            "last_seen": vehicle["time"],
            "current_place": vehicle["place"],
            "trips": len(v_trips),
            "stops": len(v_stops),
            "miles": round(sum(row["miles"] for row in v_trips), 1),
            "speeding": len(v_speed),
            "inefficient_routes": len(inefficient),
            "max_speed": max([row["speed"] for row in v_speed], default=0),
        })

    write_csv(DASH / "dashboard_interactivo_resumen.csv", summary, summary[0].keys())
    write_csv(DASH / "dashboard_interactivo_rutas.csv", [{k: v for k, v in row.items() if k != "route_points"} for row in trips], [k for k in trips[0].keys() if k != "route_points"] if trips else [])

    data = {
        "generatedAt": iso(dt.datetime.now()),
        "speedLimit": SPEED_LIMIT,
        "vehicles": current,
        "summary": summary,
        "trips": trips,
        "stops": stops,
        "speeding": speeding,
        "recommendations": [
            "Priorizar vehiculos detenidos con ignicion apagada por mucho tiempo en zonas residenciales.",
            "Revisar rutas marcadas como Ineficiente; recorren muchas mas millas que la distancia directa entre origen y destino.",
            "Configurar Google Routes API para comparar cada tramo contra rutas con trafico actual o predictivo.",
            "Enviar alertas inmediatas cuando una unidad exceda 65 mph o permanezca detenida mas de 45 minutos.",
            "Separar metricas por departamento/conductor para detectar patron operacional, no solo eventos aislados.",
        ],
    }

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RPS Medical Fleet Command</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root {{
  --navy: #092433;
  --teal: #00a7a5;
  --cyan: #38c6ff;
  --green: #53c271;
  --coral: #ff6b6b;
  --yellow: #ffd166;
  --ink: #14212b;
  --muted: #60717f;
  --panel: rgba(255,255,255,.94);
  --line: #d8e4ea;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; color: var(--ink); background: #eef7f8; letter-spacing: 0; }}
.hero {{
  min-height: 88vh;
  background: linear-gradient(90deg, rgba(5,28,43,.86), rgba(5,28,43,.56), rgba(5,28,43,.18)), url('assets/healthcare-fleet-hero.png') center/cover no-repeat;
  color: white;
  display: flex;
  align-items: end;
  padding: 34px;
}}
.hero-inner {{ max-width: 1060px; padding-bottom: 8vh; }}
.brand-lockup {{ display: flex; align-items: center; gap: 18px; flex-wrap: wrap; margin-bottom: 22px; }}
.hero-logo {{ width: min(290px, 58vw); height: auto; filter: drop-shadow(0 8px 22px rgba(0,0,0,.28)); }}
.hero h1 {{ font-size: clamp(36px, 6vw, 76px); line-height: 1; margin: 0 0 18px; letter-spacing: 0; max-width: 980px; }}
.hero p {{ font-size: 20px; max-width: 760px; line-height: 1.45; margin: 0 0 28px; }}
.hero-actions {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.btn {{ border: 0; background: var(--teal); color: white; padding: 12px 16px; border-radius: 8px; font-weight: 800; cursor: pointer; }}
.btn.secondary {{ background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.42); }}
nav {{ position: sticky; top: 0; z-index: 900; background: rgba(9,36,51,.96); color: white; padding: 10px 18px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
.nav-brand {{ display: flex; align-items: center; gap: 10px; margin-right: auto; font-weight: 900; }}
.nav-logo {{ height: 34px; width: auto; display: block; }}
nav button, select, input {{ border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; background: white; color: var(--ink); }}
nav button {{ background: #12394f; color: white; border-color: #28566b; cursor: pointer; }}
nav button.active-filter {{ background: var(--teal); border-color: var(--teal); }}
.logout-btn {{ background: #7b1f1f; border-color: #a44; }}
.auth-gate {{ position: fixed; inset: 0; z-index: 5000; display: grid; place-items: center; padding: 22px; background: linear-gradient(90deg, rgba(5,28,43,.92), rgba(5,28,43,.72)), url('assets/healthcare-fleet-hero.png') center/cover no-repeat; }}
.auth-card {{ width: min(430px, 100%); background: rgba(255,255,255,.96); border: 1px solid rgba(255,255,255,.65); border-radius: 8px; padding: 24px; box-shadow: 0 24px 80px rgba(0,0,0,.35); }}
.auth-card img {{ height: 54px; width: auto; display: block; margin-bottom: 18px; background: #092433; border-radius: 6px; padding: 8px; }}
.auth-card h2 {{ margin: 0 0 8px; font-size: 24px; }}
.auth-card p {{ margin: 0 0 18px; color: var(--muted); line-height: 1.45; }}
.auth-card label {{ display: block; font-size: 12px; text-transform: uppercase; color: var(--muted); font-weight: 900; margin-bottom: 6px; }}
.auth-card input {{ width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 12px; font-size: 16px; margin-bottom: 12px; }}
.auth-card button {{ width: 100%; border: 0; border-radius: 8px; padding: 12px; background: var(--teal); color: white; font-weight: 900; cursor: pointer; }}
.auth-error {{ min-height: 20px; color: #9b1c1c; font-weight: 800; margin-top: 10px; }}
main {{ padding: 18px; max-width: 1600px; margin: 0 auto; }}
.update-banner {{ display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-bottom: 14px; padding: 13px 15px; background: linear-gradient(90deg, #ffffff, #e5fbfa); border: 1px solid var(--line); border-left: 6px solid var(--teal); border-radius: 8px; box-shadow: 0 10px 28px rgba(4,35,48,.08); }}
.update-banner strong {{ font-size: 18px; }}
.update-banner span {{ color: var(--muted); font-weight: 800; }}
.update-time {{ color: var(--navy); font-weight: 900; }}
.metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 14px; }}
.metric {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; box-shadow: 0 10px 28px rgba(4,35,48,.08); cursor: pointer; transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease; position: relative; }}
.metric:hover {{ transform: translateY(-2px); border-color: var(--teal); box-shadow: 0 16px 34px rgba(4,35,48,.14); }}
.metric span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 800; }}
.metric strong {{ display: block; font-size: 30px; margin-top: 6px; }}
.metric em {{ display: block; margin-top: 8px; color: var(--muted); font-size: 11px; font-style: normal; font-weight: 800; }}
.metric.risk-high {{ border-top: 5px solid var(--coral); }}
.metric.risk-med {{ border-top: 5px solid var(--yellow); }}
.metric.risk-low {{ border-top: 5px solid var(--green); }}
.risk-explainer {{ display: grid; grid-template-columns: minmax(260px, .8fr) minmax(300px, 1.2fr); gap: 14px; margin-bottom: 14px; }}
.route-explainer {{ display: grid; grid-template-columns: minmax(280px, .9fr) minmax(320px, 1.1fr); gap: 14px; margin-bottom: 14px; }}
.risk-score-card {{ border-left: 6px solid var(--coral); }}
.risk-score {{ font-size: 46px; font-weight: 950; line-height: 1; margin: 8px 0; }}
.risk-breakdown {{ display: grid; gap: 8px; }}
.risk-line {{ display: grid; grid-template-columns: 150px 1fr auto; gap: 8px; align-items: center; font-size: 13px; }}
.risk-bar {{ height: 9px; border-radius: 999px; background: #e8f3f4; overflow: hidden; }}
.risk-bar b {{ display: block; height: 100%; background: linear-gradient(90deg, var(--green), var(--yellow), var(--coral)); }}
.fix-list {{ margin: 10px 0 0; padding-left: 18px; color: #21414c; }}
.explain-list {{ display: grid; gap: 9px; margin: 0; padding: 0; list-style: none; }}
.explain-list li {{ border-left: 5px solid var(--teal); background: #f6fbfc; border-radius: 7px; padding: 10px 12px; }}
.route-detail-list {{ display: grid; gap: 9px; margin: 0; padding: 0; list-style: none; max-height: 360px; overflow: auto; }}
.route-detail-list li {{ border: 1px solid var(--line); border-left: 5px solid var(--coral); border-radius: 7px; padding: 10px 12px; background: white; }}
.layout {{ display: grid; grid-template-columns: minmax(360px, 1.25fr) minmax(320px, .75fr); gap: 14px; align-items: start; }}
.panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; box-shadow: 0 10px 28px rgba(4,35,48,.08); }}
.panel h2 {{ margin: 0 0 12px; font-size: 20px; }}
#map {{ height: 620px; border-radius: 8px; border: 1px solid var(--line); }}
.ops-grid {{ display: grid; grid-template-columns: repeat(3, minmax(240px, 1fr)); gap: 14px; margin: 14px 0; }}
.ops-card {{ background: linear-gradient(135deg, #ffffff, #effbfb); border: 1px solid var(--line); border-radius: 8px; padding: 14px; box-shadow: 0 10px 28px rgba(4,35,48,.08); }}
.ops-card h3 {{ margin: 0 0 10px; font-size: 16px; }}
.ops-list {{ display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }}
.ops-list li {{ background: white; border: 1px solid #e2edf1; border-left: 5px solid var(--teal); border-radius: 6px; padding: 9px 10px; font-size: 13px; }}
.ops-list li.danger {{ border-left-color: var(--coral); }}
.ops-list li.warnline {{ border-left-color: var(--yellow); }}
.command-strip {{ display: grid; grid-template-columns: repeat(5, minmax(160px, 1fr)); gap: 12px; margin: 0 0 14px; }}
.command-item {{ background: linear-gradient(135deg, #092433, #0d4c61); color: white; border-radius: 8px; padding: 13px 14px; border: 1px solid rgba(255,255,255,.16); box-shadow: 0 12px 30px rgba(4,35,48,.14); }}
.command-item span {{ display: block; color: rgba(255,255,255,.72); font-size: 11px; text-transform: uppercase; font-weight: 900; margin-bottom: 5px; }}
.command-item strong {{ display: block; font-size: 18px; line-height: 1.2; }}
.command-item small {{ display: block; color: rgba(255,255,255,.72); margin-top: 4px; line-height: 1.25; }}
.action-grid {{ display: grid; grid-template-columns: repeat(4, minmax(190px, 1fr)); gap: 12px; margin: 14px 0; }}
.action-card {{ background: white; border: 1px solid var(--line); border-top: 5px solid var(--teal); border-radius: 8px; padding: 12px; box-shadow: 0 10px 28px rgba(4,35,48,.08); }}
.action-card.high {{ border-top-color: var(--coral); }}
.action-card.medium {{ border-top-color: var(--yellow); }}
.action-card h3 {{ margin: 0 0 8px; font-size: 15px; }}
.action-card p {{ margin: 0; color: var(--muted); line-height: 1.38; font-size: 13px; }}
.route-quality {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 12px; }}
.quality-box {{ border-radius: 8px; padding: 10px; background: #f4fbfb; border: 1px solid var(--line); }}
.quality-box b {{ display: block; font-size: 22px; }}
.quality-box span {{ color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; }}
.toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }}
.toolbar h2 {{ margin: 0 auto 0 0; }}
.toolbar button {{ border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; background: #12394f; color: white; cursor: pointer; font-weight: 800; }}
.map-note {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
.pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 9px; border-radius: 999px; background: white; border: 1px solid var(--line); font-size: 12px; color: var(--muted); }}
.dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
.charts {{ display: grid; grid-template-columns: repeat(2, minmax(260px, 1fr)); gap: 14px; margin-top: 14px; }}
canvas {{ max-height: 300px; }}
.tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; }}
.tabs button.active {{ background: var(--teal); }}
.table-wrap {{ overflow: auto; max-height: 560px; border: 1px solid var(--line); border-radius: 8px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: white; }}
th, td {{ padding: 9px 10px; border-bottom: 1px solid #e5edf1; text-align: left; vertical-align: top; }}
th {{ position: sticky; top: 0; background: #dff6f4; z-index: 1; cursor: pointer; user-select: none; }}
tr:hover td {{ background: #fff9e8; }}
.table-head {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }}
.table-head h2 {{ margin: 0 auto 0 0; }}
.row-count {{ color: var(--muted); font-size: 13px; font-weight: 800; }}
.badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-weight: 800; font-size: 12px; }}
.ok {{ background: #dff8e7; color: #15663a; }}
.warn {{ background: #fff3c4; color: #795800; }}
.bad {{ background: #ffe1df; color: #9b1c1c; }}
.info {{ background: #daf4ff; color: #075d77; }}
.rec-list {{ display: grid; gap: 9px; padding: 0; margin: 0; list-style: none; }}
.rec-list li {{ border-left: 5px solid var(--teal); background: #f4fbfb; padding: 10px 12px; border-radius: 6px; }}
.vehicle-card {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 10px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 8px; background: white; cursor: pointer; }}
.vehicle-card:hover {{ border-color: var(--teal); }}
.small {{ color: var(--muted); font-size: 12px; }}
@media (max-width: 1050px) {{ .metrics {{ grid-template-columns: repeat(2, 1fr); }} .layout, .charts, .ops-grid, .command-strip, .action-grid, .risk-explainer, .route-explainer {{ grid-template-columns: 1fr; }} #map {{ height: 520px; }} }}
@media (max-width: 620px) {{ .hero {{ padding: 22px; min-height: 82vh; }} .hero h1 {{ font-size: 38px; }} .metrics {{ grid-template-columns: 1fr; }} nav {{ align-items: stretch; }} .nav-brand {{ width: 100%; }} nav button, select, input {{ flex: 1; min-width: 120px; }} }}
</style>
</head>
<body>
<div class="auth-gate" id="authGate">
  <form class="auth-card" id="authForm">
    <img src="assets/rps-logo-white.png" alt="RPS Medical">
    <h2>Acceso protegido</h2>
    <p>Entre el password autorizado para abrir el dashboard operacional de flota.</p>
    <label for="authPassword">Password</label>
    <input id="authPassword" type="password" autocomplete="current-password" autofocus>
    <button type="submit">Entrar</button>
    <div class="auth-error" id="authError"></div>
  </form>
</div>
<section class="hero">
  <div class="hero-inner">
    <div class="brand-lockup"><img class="hero-logo" src="assets/rps-logo-white.png" alt="RPS Medical"></div>
    <h1>Fleet Command</h1>
    <p>Control interactivo de vehÃ­culos, paradas, excesos de velocidad y eficiencia de rutas para operaciones de salud y entregas crÃ­ticas.</p>
    <div class="hero-actions">
      <button class="btn" onclick="document.getElementById('dashboard').scrollIntoView({{behavior:'smooth'}})">Abrir dashboard</button>
      <button class="btn secondary" onclick="document.getElementById('recommendations').scrollIntoView({{behavior:'smooth'}})">Ver recomendaciones</button>
    </div>
  </div>
</section>
<nav>
  <div class="nav-brand"><img class="nav-logo" src="assets/rps-logo-white.png" alt="RPS Medical"><span>Fleet Command</span></div>
  <select id="vehicleFilter"><option value="all">Todos los vehÃ­culos</option></select>
  <input id="searchBox" placeholder="Buscar lugar, placa, conductor">
  <button onclick="focusAll()">Ver todos</button>
  <button data-status="moving" onclick="setStatusFilter('moving', this)">Moviendo</button>
  <button data-status="stopped" onclick="setStatusFilter('stopped', this)">Detenidos</button>
  <button data-status="risk" onclick="setStatusFilter('risk', this)">Riesgo</button>
  <button onclick="showLayer('routes')">Rutas</button>
  <button onclick="showLayer('stops')">Paradas</button>
  <button onclick="showLayer('speeding')">Velocidad</button>
  <button onclick="showLayer('heat')">Heatmap</button>
  <button class="logout-btn" onclick="logoutDashboard()">Salir</button>
</nav>
<main id="dashboard">
  <section class="update-banner">
    <div><strong>Ultima actualizacion de datos</strong><br><span>Informacion reconstruida desde SkyTrackIt y publicada en este dashboard.</span></div>
    <div class="update-time" id="lastUpdated"></div>
  </section>
  <section class="metrics" id="metrics"></section>
  <section class="risk-explainer" id="riskExplainer"></section>
  <section class="route-explainer" id="routeExplainer"></section>
  <section class="command-strip" id="commandStrip"></section>
  <div class="layout">
    <section class="panel" id="mapPanel">
      <div class="toolbar">
        <h2>Mapa operacional</h2>
        <button onclick="animateBestRoute()">Animar ruta critica</button>
        <button onclick="downloadCurrentTable()">Exportar tabla</button>
      </div>
      <div id="map"></div>
      <div class="map-note">
        <span class="pill"><span class="dot" style="background:#53c271"></span>Moviendo</span>
        <span class="pill"><span class="dot" style="background:#ff6b6b"></span>Detenido</span>
        <span class="pill"><span class="dot" style="background:#ffd166"></span>Exceso</span>
        <span class="pill"><span class="dot" style="background:#00a7a5"></span>Paradas frecuentes</span>
      </div>
    </section>
    <aside class="panel" id="vehiclePanel"><h2>VehÃ­culos localizados</h2><div id="vehicleCards"></div></aside>
  </div>
  <section class="ops-grid" id="opsPanel">
    <div class="ops-card"><h3>Alertas ahora</h3><ul class="ops-list" id="alertList"></ul></div>
    <div class="ops-card"><h3>Rutas a revisar</h3><ul class="ops-list" id="routeList"></ul></div>
    <div class="ops-card"><h3>Zonas con mas paradas</h3><ul class="ops-list" id="hotspotList"></ul></div>
  </section>
  <section class="panel" id="actionPlan">
    <h2>Plan de accion recomendado</h2>
    <div class="action-grid" id="actionGrid"></div>
    <div class="route-quality" id="routeQuality"></div>
  </section>
  <section class="charts" id="chartsPanel">
    <div class="panel"><h2>Millas por vehÃ­culo</h2><canvas id="milesChart"></canvas></div>
    <div class="panel"><h2>Rutas ineficientes</h2><canvas id="effChart"></canvas></div>
    <div class="panel"><h2>Excesos de velocidad</h2><canvas id="speedChart"></canvas></div>
    <div class="panel"><h2>Paradas por vehÃ­culo</h2><canvas id="stopsChart"></canvas></div>
  </section>
  <section class="panel" id="recommendations"><h2>Recomendaciones para mejorar operaciÃ³n</h2><ul class="rec-list" id="recList"></ul></section>
  <div class="tabs">
    <button class="btn active" data-table="summary">Resumen</button>
    <button class="btn" data-table="trips">Rutas</button>
    <button class="btn" data-table="stops">Paradas</button>
    <button class="btn" data-table="speeding">Excesos</button>
  </div>
  <section class="panel" id="tablePanel"><div class="table-head"><h2 id="tableTitle">Resumen</h2><span class="row-count" id="rowCount"></span></div><div class="table-wrap" id="table"></div></section>
</main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script id="fleetData" type="application/json">{payload}</script>
<script>
const DASHBOARD_PASSWORD = 'melvinmelvin';
function unlockDashboard() {{
  document.getElementById('authGate').style.display = 'none';
  document.body.style.overflow = '';
}}
function logoutDashboard() {{
  sessionStorage.removeItem('rpsFleetAuth');
  document.getElementById('authGate').style.display = 'grid';
  document.body.style.overflow = 'hidden';
  document.getElementById('authPassword').value = '';
  document.getElementById('authPassword').focus();
}}
document.body.style.overflow = 'hidden';
if (sessionStorage.getItem('rpsFleetAuth') === 'ok') unlockDashboard();
document.getElementById('authForm').addEventListener('submit', event => {{
  event.preventDefault();
  const value = document.getElementById('authPassword').value;
  if (value === DASHBOARD_PASSWORD) {{
    sessionStorage.setItem('rpsFleetAuth', 'ok');
    document.getElementById('authError').textContent = '';
    unlockDashboard();
  }} else {{
    document.getElementById('authError').textContent = 'Password incorrecto.';
  }}
}});
const data = JSON.parse(document.getElementById('fleetData').textContent);
document.title = `RPS Fleet - ${{data.vehicles.length}} vehÃ­culos`;
let selected = 'all';
let activeLayer = 'routes';
let statusFilter = 'all';
let sortState = {{ field: null, dir: 1 }};
const colors = ['#00a7a5','#38c6ff','#ff6b6b','#ffd166','#53c271','#7b61ff','#ff8a3d','#2f6f9f','#b94a48','#16a085'];
if (typeof L === 'undefined' || typeof Chart === 'undefined') {{
  document.getElementById('map').innerHTML = '<div style="padding:24px;font-weight:800;color:#9b1c1c">No cargaron las librerÃ­as de mapa/grÃ¡ficas. Verifica conexiÃ³n a internet o abre el ZIP extraÃ­do completo.</div>';
  throw new Error('Leaflet or Chart.js did not load');
}}
const map = L.map('map', {{ scrollWheelZoom: true }}).setView([18.28, -66.45], 10);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '&copy; OpenStreetMap' }}).addTo(map);
const markerLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const stopLayer = L.layerGroup().addTo(map);
const speedLayer = L.layerGroup().addTo(map);
const heatLayer = L.layerGroup().addTo(map);
let playbackMarker = null;

function rows() {{
  const query = document.getElementById('searchBox').value.toLowerCase();
  const riskSerials = new Set(data.summary.filter(s => Number(s.speeding || 0) || Number(s.inefficient_routes || 0) || s.current_status === 'Detenido').map(s => s.serial));
  const movingSerials = new Set(data.vehicles.filter(v => Number(v.speed || 0) > 2).map(v => v.serial));
  const statusKeep = item => statusFilter === 'all' || (statusFilter === 'moving' && movingSerials.has(item.serial)) || (statusFilter === 'stopped' && !movingSerials.has(item.serial)) || (statusFilter === 'risk' && riskSerials.has(item.serial));
  const keep = item => (selected === 'all' || item.serial === selected) && statusKeep(item) && JSON.stringify(item).toLowerCase().includes(query);
  return {{
    vehicles: data.vehicles.filter(keep),
    summary: data.summary.filter(keep),
    trips: data.trips.filter(keep),
    stops: data.stops.filter(keep),
    speeding: data.speeding.filter(keep)
  }};
}}
function badge(value) {{
  const cls = value === 'Ineficiente' || value === 'Detenido' || value === 'Alto' ? 'bad' : value === 'Revisar' || value === 'Medio' ? 'warn' : value === 'Moviendo' || value === 'Eficiente' || value === 'Bajo' ? 'ok' : 'info';
  return `<span class="badge ${{cls}}">${{value || ''}}</span>`;
}}
function riskScore(row) {{
  return Math.min(100, Math.round(Number(row.speeding || 0) * 12 + Number(row.inefficient_routes || 0) * 8 + Number(row.stops || 0) * 0.08 + (row.current_status === 'Detenido' ? 10 : 0)));
}}
function fleetRiskParts(r) {{
  const speeding = r.speeding.length;
  const inefficient = r.trips.filter(t => t.efficiency === 'Ineficiente').length;
  const review = r.trips.filter(t => t.efficiency === 'Revisar').length;
  const stale = r.vehicles.filter(v => ageMinutes(v.time) > 90).length;
  const stoppedLong = r.vehicles.filter(v => Number(v.speed || 0) <= 2 && ageMinutes(v.time) > 45).length;
  const score = Math.min(100, Math.round(speeding * 2.4 + inefficient * 1.2 + review * .45 + stale * 7 + stoppedLong * 4));
  return {{ speeding, inefficient, review, stale, stoppedLong, score }};
}}
function fleetRiskText(score) {{
  if (score >= 65) return 'Alto';
  if (score >= 30) return 'Medio';
  return 'Bajo';
}}
function metricInfo(label) {{
  const info = {{
    'VehÃ­culos': ['Total de unidades localizadas en la data actual.', 'vehiclePanel'],
    'Moviendo': ['Unidades con velocidad mayor de 2 mph en su ultima lectura.', 'vehiclePanel'],
    'Detenidos': ['Unidades con velocidad de 2 mph o menos en su ultima lectura.', 'vehiclePanel'],
    'Rutas': ['Viajes completos detectados por eventos de ignicion/salida-llegada.', 'tablePanel'],
    'Millas': ['Millas recorridas en las rutas cargadas en el periodo actual.', 'chartsPanel'],
    'Excesos': ['Eventos donde la unidad supero el umbral configurado de 65 mph.', 'opsPanel'],
    'Rutas ineficientes': ['Rutas donde las millas recorridas fueron mucho mayores que la distancia directa estimada.', 'opsPanel'],
    'Sin seÃ±al >90m': ['Unidades cuya ultima senal tiene mas de 90 minutos.', 'opsPanel'],
    'Riesgo operacional': ['Indice interno: excesos + rutas ineficientes + unidades sin senal + paradas largas. Click para ver como bajarlo.', 'riskExplainer']
  }};
  return info[label] || ['', 'dashboard'];
}}
function jumpTo(sectionId) {{
  const el = document.getElementById(sectionId);
  if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
}}
function riskLabel(score) {{
  if (score >= 65) return badge('Alto');
  if (score >= 30) return badge('Medio');
  return badge('Bajo');
}}
function setStatusFilter(value, btn) {{
  statusFilter = statusFilter === value ? 'all' : value;
  document.querySelectorAll('nav button[data-status]').forEach(b => b.classList.remove('active-filter'));
  if (statusFilter !== 'all' && btn) btn.classList.add('active-filter');
  render();
}}
function renderFilters() {{
  const select = document.getElementById('vehicleFilter');
  data.vehicles.forEach(v => {{
    const opt = document.createElement('option');
    opt.value = v.serial; opt.textContent = v.vehicle;
    select.appendChild(opt);
  }});
  select.addEventListener('change', e => {{ selected = e.target.value; render(); }});
  document.getElementById('searchBox').addEventListener('input', render);
}}
function renderMetrics(r) {{
  const moving = r.vehicles.filter(v => v.speed > 2).length;
  const stopped = r.vehicles.length - moving;
  const inefficient = r.trips.filter(t => t.efficiency === 'Ineficiente').length;
  const speeding = r.speeding.length;
  const stale = r.vehicles.filter(v => ageMinutes(v.time) > 90).length;
  const miles = r.trips.reduce((sum, t) => sum + Number(t.miles || 0), 0).toFixed(1);
  const risk = fleetRiskParts(r);
  const riskClass = risk.score >= 65 ? 'risk-high' : risk.score >= 30 ? 'risk-med' : 'risk-low';
  document.getElementById('metrics').innerHTML = [
    ['VehÃ­culos', r.vehicles.length, '', 'Ver unidades localizadas'],
    ['Moviendo', moving, 'risk-low', 'Ver unidades moviendo'],
    ['Detenidos', stopped, stopped ? 'risk-med' : 'risk-low', 'Ver unidades detenidas'],
    ['Rutas', r.trips.length, '', 'Ver tabla de rutas'],
    ['Millas', miles, '', 'Ver graficas'],
    ['Excesos', speeding, speeding ? 'risk-high' : 'risk-low', 'Ver excesos'],
    ['Rutas ineficientes', inefficient, inefficient ? 'risk-high' : 'risk-low', 'Ver rutas a revisar'],
    ['Sin seÃ±al >90m', stale, stale ? 'risk-high' : 'risk-low', 'Ver alertas'],
    ['Riesgo operacional', `${{fleetRiskText(risk.score)}} (${{risk.score}}/100)`, riskClass, 'Ver definicion y acciones']
  ].map(m => {{
    const [hint, target] = metricInfo(m[0]);
    return `<div class="metric ${{m[2]}}" title="${{hint}}" onclick="jumpTo('${{target}}')"><span>${{m[0]}}</span><strong>${{m[1]}}</strong><em>${{m[3]}}</em></div>`;
  }}).join('');
}}
function renderRiskExplainer(r) {{
  const risk = fleetRiskParts(r);
  const label = fleetRiskText(risk.score);
  const parts = [
    ['Excesos de velocidad', risk.speeding, Math.min(100, risk.speeding * 6), 'Bajar con alertas al conductor y coaching por unidad.'],
    ['Rutas ineficientes', risk.inefficient, Math.min(100, risk.inefficient * 4), 'Comparar contra Google Routes y validar desvÃ­os autorizados.'],
    ['Rutas a revisar', risk.review, Math.min(100, risk.review * 2), 'Revisar si hubo trÃ¡fico, entregas extra o mala secuencia.'],
    ['Sin seÃ±al >90 min', risk.stale, Math.min(100, risk.stale * 18), 'Confirmar GPS, baterÃ­a, cobertura o unidad apagada.'],
    ['Detenidos >45 min', risk.stoppedLong, Math.min(100, risk.stoppedLong * 14), 'Llamar conductor o validar entrega/mantenimiento.']
  ];
  document.getElementById('riskExplainer').innerHTML = `
    <div class="panel risk-score-card">
      <h2>Que significa Riesgo operacional</h2>
      <div class="risk-score">${{label}} <span class="small">${{risk.score}}/100</span></div>
      <p class="small">Es un indice interno del dashboard. No viene directo de SkyTrackIt. Se calcula con eventos que pueden afectar seguridad, servicio y control: excesos, rutas ineficientes, unidades sin senal y paradas largas. Mientras mas eventos abiertos existan, mas alto sube.</p>
      <ul class="fix-list">
        <li><b>Velocidad:</b> bajar excesos sobre 65 mph con llamada inmediata, coaching y seguimiento por unidad.</li>
        <li><b>GPS:</b> resolver unidades sin senal revisando equipo, bateria, cobertura o si la unidad esta apagada.</li>
        <li><b>Paradas:</b> validar paradas mayores de 45 minutos y documentar si son entrega, almuerzo, espera o mantenimiento.</li>
        <li><b>Rutas:</b> convertir rutas ineficientes en rutas eficientes bajando millas, reduciendo vueltas y agrupando entregas por zona.</li>
      </ul>
    </div>
    <div class="panel">
      <h2>Por que salio ${{label}}</h2>
      <div class="risk-breakdown">
        ${{parts.map(p => `<div class="risk-line"><b>${{p[0]}}</b><div class="risk-bar"><b style="width:${{p[2]}}%"></b></div><span>${{p[1]}}</span></div><div class="small">${{p[3]}}</div>`).join('')}}
      </div>
    </div>`;
}}
function routeCause(trip) {{
  if (!trip || !trip.efficiency_ratio) return 'No hay distancia suficiente o falta odometro/GPS para evaluar.';
  if (trip.efficiency === 'Eficiente') return 'La ruta esta dentro del margen operacional: no se aleja demasiado de la distancia directa.';
  if (trip.efficiency === 'Revisar') return `La ruta recorrio ${{trip.efficiency_ratio}}x la distancia directa. Puede ser trafico, entrega adicional, desvio autorizado o mala secuencia de paradas.`;
  return `La ruta recorrio ${{trip.efficiency_ratio}}x la distancia directa. Eso sugiere desvio largo, secuencia ineficiente, parada no planificada o ruta tomada sin optimizar.`;
}}
function routeFix(trip) {{
  if (!trip || !trip.efficiency_ratio) return 'Validar que el GPS y odometro esten reportando bien antes de evaluar.';
  const target = Number(trip.straight_miles || 0) ? (Number(trip.straight_miles) * 1.35).toFixed(1) : 'N/A';
  const saved = Number(trip.potential_saved_miles || 0).toFixed(1);
  if (trip.efficiency === 'Eficiente') return 'Mantener esa ruta como referencia para despachos parecidos.';
  if (trip.efficiency === 'Revisar') return `Para hacerla eficiente: planificar cerca de ${{target}} millas o menos, validar trafico antes de salir y ordenar paradas por zona. Ahorro estimado: ${{saved}} mi.`;
  return `Alternativa: recalcular antes del despacho, evitar regresos sobre la misma zona, agrupar entregas cercanas y comparar con Google Routes. Meta eficiente: ${{target}} millas o menos. Ahorro estimado: ${{saved}} mi.`;
}}
function renderRouteExplainer(r) {{
  const badRoutes = [...r.trips]
    .filter(t => t.efficiency === 'Ineficiente' || t.efficiency === 'Revisar')
    .sort((a,b) => Number(b.efficiency_ratio || 0) - Number(a.efficiency_ratio || 0))
    .slice(0, 6);
  document.getElementById('routeExplainer').innerHTML = `
    <div class="panel">
      <h2>Por que una ruta sale ineficiente</h2>
      <ul class="explain-list">
        <li><b>Criterio actual:</b> se compara millas recorridas vs distancia directa entre salida y llegada.</li>
        <li><b>Eficiente:</b> hasta 1.35x la distancia directa.</li>
        <li><b>Revisar:</b> mayor de 1.35x hasta 1.80x.</li>
        <li><b>Ineficiente:</b> mayor de 1.80x. No significa culpa automaticamente; significa que hay que revisar trafico, desvio, paradas extra o secuencia.</li>
        <li><b>Para ser eficiente:</b> bajar millas, reducir vueltas, agrupar paradas cercanas y validar ruta con trafico antes de salir.</li>
      </ul>
    </div>
    <div class="panel">
      <h2>Alternativas para las rutas marcadas</h2>
      <ul class="route-detail-list">
        ${{badRoutes.length ? badRoutes.map(t => `<li><b>${{t.vehicle}}</b> ${{badge(t.efficiency)}}<br><span class="small">${{t.departure}} · ${{t.miles}} mi reales vs ${{t.straight_miles}} mi directa · ratio ${{t.efficiency_ratio}}x</span><br><b>Causa:</b> ${{routeCause(t)}}<br><b>Accion:</b> ${{routeFix(t)}}</li>`).join('') : '<li>No hay rutas ineficientes con el filtro actual.</li>'}}
      </ul>
    </div>`;
}}
function latestVehicleTime(r) {{
  const times = r.vehicles.map(v => new Date(String(v.time).replace(' ', 'T'))).filter(t => !Number.isNaN(t.getTime()));
  return times.length ? new Date(Math.max(...times.map(t => t.getTime()))) : null;
}}
function generatedDate() {{
  const parsed = new Date(String(data.generatedAt).replace(' ', 'T'));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}}
function formatDateTime(value) {{
  return value ? value.toLocaleString('es-PR', {{ year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' }}) : 'Sin timestamp disponible';
}}
function routeQuality(r) {{
  const efficient = r.trips.filter(t => t.efficiency === 'Eficiente').length;
  const review = r.trips.filter(t => t.efficiency === 'Revisar').length;
  const bad = r.trips.filter(t => t.efficiency === 'Ineficiente').length;
  return {{ efficient, review, bad }};
}}
function topSummary(r, field) {{
  return [...r.summary].sort((a,b) => Number(b[field] || 0) - Number(a[field] || 0))[0] || null;
}}
function renderCommandStrip(r) {{
  const latest = latestVehicleTime(r);
  const generated = generatedDate();
  const freshest = latest ? elapsedLabel(ageMinutes(latest.toISOString().replace('T', ' '))) : 'sin data';
  const worstRoute = [...r.trips].filter(t => Number(t.efficiency_ratio || 0)).sort((a,b) => Number(b.efficiency_ratio || 0) - Number(a.efficiency_ratio || 0))[0];
  const topStops = topSummary(r, 'stops');
  const topSpeed = topSummary(r, 'speeding');
  const quality = routeQuality(r);
  document.getElementById('commandStrip').innerHTML = [
    ['Ultima senal', latest ? latest.toLocaleString() : 'Sin data', `hace ${{freshest}}`],
    ['Datos actualizados', formatDateTime(generated), 'refresco cada 5 min'],
    ['Ruta peor ratio', worstRoute ? `${{worstRoute.efficiency_ratio}}x` : 'N/A', worstRoute ? worstRoute.vehicle : 'Sin rutas'],
    ['Mas paradas', topStops ? topStops.vehicle : 'N/A', topStops ? `${{topStops.stops}} paradas` : 'Sin paradas'],
    ['Mas velocidad', topSpeed ? topSpeed.vehicle : 'N/A', topSpeed ? `${{topSpeed.speeding}} excesos` : 'Sin excesos']
  ].map(item => `<div class="command-item"><span>${{item[0]}}</span><strong>${{item[1]}}</strong><small>${{item[2]}}</small></div>`).join('');
  document.getElementById('routeQuality').innerHTML = [
    ['Eficientes', quality.efficient, 'ok'],
    ['Revisar', quality.review, 'warn'],
    ['Ineficientes', quality.bad, 'bad']
  ].map(item => `<div class="quality-box"><span>${{item[0]}}</span><b>${{item[1]}}</b>${{badge(item[0])}}</div>`).join('');
}}
function renderLastUpdated() {{
  const generated = generatedDate();
  document.getElementById('lastUpdated').innerHTML = `${{formatDateTime(generated)}} <span>Actualiza cada 5 minutos</span>`;
}}
function renderActionPlan(r) {{
  const stoppedLong = r.vehicles.filter(v => Number(v.speed || 0) <= 2 && ageMinutes(v.time) > 45).sort((a,b) => ageMinutes(b.time) - ageMinutes(a.time))[0];
  const worstRoute = [...r.trips].filter(t => t.efficiency === 'Ineficiente' || t.efficiency === 'Revisar').sort((a,b) => Number(b.efficiency_ratio || 0) - Number(a.efficiency_ratio || 0))[0];
  const fastest = [...r.speeding].sort((a,b) => Number(b.speed || 0) - Number(a.speed || 0))[0];
  const stale = r.vehicles.filter(v => ageMinutes(v.time) > 90).sort((a,b) => ageMinutes(b.time) - ageMinutes(a.time))[0];
  const cards = [
    stoppedLong ? ['high', 'Minimizar paradas largas', `${{stoppedLong.vehicle}} lleva aprox. ${{elapsedLabel(ageMinutes(stoppedLong.time))}} detenido. Para bajar riesgo: llamar conductor, confirmar motivo y cerrar la parada como entrega, espera, mantenimiento o fuera de servicio.`] : ['low', 'Paradas largas', 'No hay unidades detenidas por mas de 45 minutos con el filtro actual.'],
    worstRoute ? ['medium', 'Hacer ruta eficiente', `${{worstRoute.vehicle}} marco ratio ${{worstRoute.efficiency_ratio}}x. Causa: ${{routeCause(worstRoute)}} Accion: ${{routeFix(worstRoute)}}`] : ['low', 'Eficiencia de rutas', 'No hay rutas marcadas como revisar/ineficiente en el filtro actual.'],
    fastest ? ['high', 'Bajar riesgo de velocidad', `${{fastest.vehicle}} llego a ${{fastest.speed}} mph. Para minimizar: llamar al conductor, documentar el tramo, confirmar si fue emergencia autorizada y monitorear la proxima ruta.`] : ['low', 'Velocidad', 'No hay excesos de velocidad con el filtro actual.'],
    stale ? ['medium', 'Recuperar senal GPS', `${{stale.vehicle}} lleva ${{elapsedLabel(ageMinutes(stale.time))}} sin reporte reciente. Para minimizar: verificar equipo, bateria, cobertura y confirmar ubicacion manualmente.`] : ['low', 'Senal GPS', 'Todas las unidades filtradas reportan dentro de la ventana esperada.']
  ];
  document.getElementById('actionGrid').innerHTML = cards.map(c => `<div class="action-card ${{c[0]}}"><h3>${{c[1]}}</h3><p>${{c[2]}}</p></div>`).join('');
}}
function renderMap(r) {{
  markerLayer.clearLayers(); routeLayer.clearLayers(); stopLayer.clearLayers(); speedLayer.clearLayers(); heatLayer.clearLayers();
  const bounds = [];
  r.trips.forEach((t, idx) => {{
    if (!t.route_points || t.route_points.length < 2) return;
    const color = t.efficiency === 'Ineficiente' ? '#ff6b6b' : t.efficiency === 'Revisar' ? '#ffd166' : colors[idx % colors.length];
    const poly = L.polyline(t.route_points, {{ color, weight: 4, opacity: .72 }}).bindPopup(`<b>${{t.vehicle}}</b><br>${{t.departure}} â†’ ${{t.arrival}}<br>${{t.miles}} mi Â· ${{badge(t.efficiency)}}`);
    routeLayer.addLayer(poly); t.route_points.forEach(p => bounds.push(p));
  }});
  r.vehicles.forEach(v => {{
    if (!v.lat || !v.lng) return;
    const color = v.speed > 2 ? '#53c271' : '#ff6b6b';
    const icon = L.divIcon({{ className: '', html: `<div style="width:18px;height:18px;background:${{color}};border:3px solid white;border-radius:50%;box-shadow:0 2px 10px rgba(0,0,0,.35)"></div>`, iconSize:[18,18] }});
    const m = L.marker([v.lat, v.lng], {{ icon }}).bindPopup(`<b>${{v.vehicle}}</b><br>${{badge(v.speed > 2 ? 'Moviendo' : 'Detenido')}}<br>${{v.place || ''}}<br>${{v.time}}<br>${{v.speed}} mph`);
    markerLayer.addLayer(m); bounds.push([v.lat, v.lng]);
  }});
  r.stops.forEach(s => {{ if (s.lat && s.lng) stopLayer.addLayer(L.circleMarker([s.lat, s.lng], {{ radius:5, color:'#00a7a5', fillOpacity:.55 }}).bindPopup(`<b>Parada</b><br>${{s.vehicle}}<br>${{s.time}}<br>${{s.place}}`)); }});
  r.speeding.forEach(s => {{ if (s.lat && s.lng) speedLayer.addLayer(L.circleMarker([s.lat, s.lng], {{ radius:7, color:'#ffd166', fillColor:'#ff6b6b', fillOpacity:.8 }}).bindPopup(`<b>Exceso</b><br>${{s.vehicle}}<br>${{s.time}}<br>${{s.speed}} mph<br>${{s.place}}`)); }});
  hotspots(r).forEach(h => heatLayer.addLayer(L.circle([h.lat, h.lng], {{ radius: Math.min(1200, 120 + h.count * 18), color:'#00a7a5', fillColor:'#00a7a5', fillOpacity:.18, weight:2 }}).bindPopup(`<b>Zona frecuente</b><br>${{h.count}} paradas<br>${{h.place}}`)));
  showLayer(activeLayer);
  if (bounds.length) map.fitBounds(bounds, {{ padding:[28,28] }});
}}
function showLayer(layer) {{
  activeLayer = layer;
  [routeLayer, stopLayer, speedLayer, heatLayer].forEach(l => map.removeLayer(l));
  if (layer === 'routes') routeLayer.addTo(map);
  if (layer === 'stops') stopLayer.addTo(map);
  if (layer === 'speeding') speedLayer.addTo(map);
  if (layer === 'heat') heatLayer.addTo(map);
}}
function focusAll() {{ selected = 'all'; statusFilter = 'all'; document.getElementById('vehicleFilter').value = 'all'; document.getElementById('searchBox').value = ''; document.querySelectorAll('nav button[data-status]').forEach(b => b.classList.remove('active-filter')); render(); }}
function renderCards(r) {{
  const bySerial = Object.fromEntries(data.summary.map(s => [s.serial, s]));
  document.getElementById('vehicleCards').innerHTML = r.vehicles.map(v => {{
    const score = riskScore(bySerial[v.serial] || {{}});
    return `<div class="vehicle-card" onclick="selectVehicle('${{v.serial}}')"><div><b>${{v.vehicle}}</b><div class="small">${{v.place || 'Sin direcciÃ³n'}}<br>${{v.time}}</div></div><div>${{badge(v.speed > 2 ? 'Moviendo' : 'Detenido')}}<div class="small">${{v.speed}} mph Â· Riesgo ${{score}}</div></div></div>`;
  }}).join('');
}}
function selectVehicle(serial) {{ selected = serial; document.getElementById('vehicleFilter').value = serial; render(); }}
let charts = [];
function makeChart(id, label, rows, field, color) {{
  const ctx = document.getElementById(id);
  const sorted = [...rows].sort((a,b) => Number(b[field]||0) - Number(a[field]||0)).slice(0,10);
  const chart = new Chart(ctx, {{ type:'bar', data: {{ labels: sorted.map(r => r.vehicle), datasets: [{{ label, data: sorted.map(r => Number(r[field]||0)), backgroundColor: color }}] }}, options: {{ responsive:true, plugins:{{ legend:{{ display:false }} }}, scales:{{ x:{{ ticks:{{ maxRotation:45, minRotation:25 }} }} }} }} }});
  charts.push(chart);
}}
function renderCharts(r) {{
  charts.forEach(c => c.destroy()); charts = [];
  makeChart('milesChart', 'Millas', r.summary, 'miles', '#00a7a5');
  makeChart('effChart', 'Rutas ineficientes', r.summary, 'inefficient_routes', '#ff6b6b');
  makeChart('speedChart', 'Excesos', r.summary, 'speeding', '#ffd166');
  makeChart('stopsChart', 'Paradas', r.summary, 'stops', '#53c271');
}}
function renderRecs() {{ document.getElementById('recList').innerHTML = data.recommendations.map(r => `<li>${{r}}</li>`).join(''); }}
function ageMinutes(value) {{
  const t = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(t.getTime())) return 0;
  const generated = new Date(String(data.generatedAt).replace(' ', 'T'));
  const baseline = Number.isNaN(generated.getTime()) ? new Date() : generated;
  return Math.max(0, (baseline.getTime() - t.getTime()) / 60000);
}}
function elapsedLabel(minutes) {{
  if (minutes >= 1440) return `${{Math.floor(minutes/1440)}}d ${{Math.floor((minutes%1440)/60)}}h`;
  if (minutes >= 60) return `${{Math.floor(minutes/60)}}h ${{Math.floor(minutes%60)}}m`;
  return `${{Math.floor(minutes)}}m`;
}}
function hotspots(r) {{
  const grouped = new Map();
  r.stops.forEach(s => {{
    if (!s.lat || !s.lng) return;
    const key = `${{Number(s.lat).toFixed(3)}},${{Number(s.lng).toFixed(3)}}`;
    const item = grouped.get(key) || {{ lat:Number(s.lat), lng:Number(s.lng), count:0, place:s.place || 'Sin direccion' }};
    item.count += 1; grouped.set(key, item);
  }});
  return [...grouped.values()].sort((a,b) => b.count - a.count).slice(0, 8);
}}
function renderOps(r) {{
  const alerts = [];
  r.vehicles.forEach(v => {{
    const mins = ageMinutes(v.time);
    if (v.speed <= 2 && mins > 45) alerts.push({{cls:'danger', text:`${{v.vehicle}} detenido aprox. ${{elapsedLabel(mins)}} - ${{v.place || 'sin direccion'}}`}});
    if (mins > 90) alerts.push({{cls:'warnline', text:`${{v.vehicle}} sin reporte reciente por ${{elapsedLabel(mins)}}`}});
  }});
  r.speeding.slice(0, 5).forEach(s => alerts.push({{cls:'warnline', text:`${{s.vehicle}} exceso: ${{s.speed}} mph (+${{s.over}}) en ${{s.place}}`}}));
  document.getElementById('alertList').innerHTML = (alerts.length ? alerts : [{{cls:'', text:'Sin alertas crÃ­ticas con el filtro actual.'}}]).slice(0,8).map(a => `<li class="${{a.cls}}">${{a.text}}</li>`).join('');

  const routeRows = r.trips.filter(t => t.efficiency === 'Ineficiente' || t.efficiency === 'Revisar').sort((a,b) => Number(b.efficiency_ratio || 0) - Number(a.efficiency_ratio || 0)).slice(0, 8);
  document.getElementById('routeList').innerHTML = (routeRows.length ? routeRows.map(t => `<li class="${{t.efficiency === 'Ineficiente' ? 'danger' : 'warnline'}}"><b>${{t.vehicle}}</b> ${{badge(t.efficiency)}}<br>${{t.departure}} - ${{t.miles}} mi vs ${{t.straight_miles}} mi directa - ratio ${{t.efficiency_ratio}}x<br><span class="small"><b>Causa:</b> ${{routeCause(t)}}<br><b>Alternativa:</b> ${{routeFix(t)}}</span></li>`) : ['<li>No hay rutas marcadas para revision.</li>']).join('');

  const hotRows = hotspots(r);
  document.getElementById('hotspotList').innerHTML = (hotRows.length ? hotRows.map(h => `<li><b>${{h.count}} paradas</b><br>${{h.place}}</li>`) : ['<li>No hay paradas en el filtro actual.</li>']).join('');
}}
function animateBestRoute() {{
  const r = rows();
  const candidates = r.trips.filter(t => t.route_points && t.route_points.length > 3).sort((a,b) => Number(b.efficiency_ratio || b.miles || 0) - Number(a.efficiency_ratio || a.miles || 0));
  const trip = candidates[0];
  if (!trip) return;
  showLayer('routes');
  const pts = trip.route_points;
  let i = 0;
  if (playbackMarker) map.removeLayer(playbackMarker);
  playbackMarker = L.circleMarker(pts[0], {{ radius:10, color:'#092433', fillColor:'#38c6ff', fillOpacity:1, weight:3 }}).addTo(map).bindPopup(`<b>Playback</b><br>${{trip.vehicle}}`).openPopup();
  map.fitBounds(pts, {{ padding:[34,34] }});
  const timer = setInterval(() => {{
    i += 1;
    if (i >= pts.length) {{ clearInterval(timer); return; }}
    playbackMarker.setLatLng(pts[i]);
  }}, Math.max(60, 1600 / pts.length));
}}
function downloadCurrentTable() {{
  const r = rows();
  const configs = {{
    summary: ['vehicle','plate','current_status','last_seen','current_place','trips','stops','miles','speeding','inefficient_routes'],
    trips: ['vehicle','departure','arrival','duration_min','origin','destination','miles','straight_miles','efficiency_ratio','efficiency','why_inefficient','efficient_action','potential_saved_miles'],
    stops: ['vehicle','time','place','speed'],
    speeding: ['vehicle','time','speed','over','place']
  }};
  const fields = configs[tableName];
  const csv = [fields.join(',')].concat(r[tableName].map(row => fields.map(f => `"${{String(row[f] ?? '').replaceAll('"','""')}}"`).join(','))).join('\\n');
  const blob = new Blob([csv], {{type:'text/csv;charset=utf-8'}});
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), {{href:url, download:`rps_${{tableName}}.csv`}});
  a.click(); URL.revokeObjectURL(url);
}}
function renderTable(name, r) {{
  const configs = {{
    summary: ['vehicle','plate','current_status','risk','last_seen','current_place','trips','stops','miles','speeding','inefficient_routes'],
    trips: ['vehicle','departure','arrival','duration_min','origin','destination','miles','straight_miles','efficiency_ratio','efficiency','why_inefficient','efficient_action','potential_saved_miles'],
    stops: ['vehicle','time','place','speed'],
    speeding: ['vehicle','time','speed','over','place']
  }};
  const labels = {{
    vehicle:'Vehiculo', plate:'Tablilla', current_status:'Estado', risk:'Riesgo', last_seen:'Ultimo reporte', current_place:'Ubicacion',
    trips:'Rutas', stops:'Paradas', miles:'Millas', speeding:'Excesos', inefficient_routes:'Rutas ineficientes',
    departure:'Salida', arrival:'Llegada', duration_min:'Min', origin:'Origen', destination:'Destino', straight_miles:'Mi directa',
    efficiency_ratio:'Ratio', efficiency:'Eficiencia', why_inefficient:'Por que', efficient_action:'Alternativa', potential_saved_miles:'Ahorro mi', time:'Hora', place:'Lugar', speed:'Velocidad', over:'Sobre limite'
  }};
  const titles = {{ summary:'Resumen', trips:'Rutas', stops:'Paradas', speeding:'Excesos de velocidad' }};
  const fields = configs[name];
  const rowsForTable = r[name].map(row => name === 'summary' ? {{...row, risk:riskScore(row)}} : row);
  if (sortState.field) {{
    rowsForTable.sort((a,b) => {{
      const av = a[sortState.field] ?? '';
      const bv = b[sortState.field] ?? '';
      const an = Number(av), bn = Number(bv);
      if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * sortState.dir;
      return String(av).localeCompare(String(bv)) * sortState.dir;
    }});
  }}
  document.getElementById('tableTitle').textContent = titles[name];
  document.getElementById('rowCount').textContent = `${{rowsForTable.length}} registros`;
  document.getElementById('table').innerHTML = `<table><thead><tr>${{fields.map(f => `<th onclick="sortTable('${{f}}')">${{labels[f] || f}} ${{sortState.field === f ? (sortState.dir > 0 ? 'â–²' : 'â–¼') : ''}}</th>`).join('')}}</tr></thead><tbody>${{rowsForTable.map(row => `<tr>${{fields.map(f => `<td>${{formatCell(f, row[f], row)}}</td>`).join('')}}</tr>`).join('')}}</tbody></table>`;
}}
function formatCell(field, value, row) {{
  if (field === 'efficiency' || field === 'current_status') return badge(value);
  if (field === 'risk') return `${{riskLabel(Number(value || 0))}} <span class="small">${{value}}</span>`;
  return value ?? '';
}}
function sortTable(field) {{
  sortState = sortState.field === field ? {{field, dir: sortState.dir * -1}} : {{field, dir: 1}};
  render();
}}
let tableName = 'summary';
document.querySelectorAll('.tabs button').forEach(btn => btn.addEventListener('click', () => {{ document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active')); btn.classList.add('active'); tableName = btn.dataset.table; sortState = {{field:null, dir:1}}; render(); }}));
function render() {{ const r = rows(); renderLastUpdated(); renderMetrics(r); renderRiskExplainer(r); renderRouteExplainer(r); renderCommandStrip(r); renderActionPlan(r); renderMap(r); renderCards(r); renderCharts(r); renderRecs(); renderOps(r); renderTable(tableName, r); }}
renderFilters(); render();
</script>
</body>
</html>"""
    (DASH / "index.html").write_text(page, encoding="utf-8")
    (DASH / "dashboard_data.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(DASH / "index.html")
    print(f"vehicles={len(current)} trips={len(trips)} stops={len(stops)} speeding={len(speeding)}")


if __name__ == "__main__":
    main()




