from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "dashboard_data.json"
INDEX_PATH = ROOT / "index.html"
PUERTO_RICO = timezone(timedelta(hours=-4), name="AST")


def main():
    generated_at = datetime.now(PUERTO_RICO).isoformat(timespec="seconds")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    data["generatedAt"] = generated_at
    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    html = INDEX_PATH.read_text(encoding="utf-8")
    match = re.search(r'(<script id="fleetData" type="application/json">)(.*?)(</script>)', html, re.DOTALL)
    if not match:
        raise RuntimeError("Could not find embedded fleetData JSON in index.html")
    embedded = json.loads(match.group(2))
    embedded["generatedAt"] = generated_at
    replacement = match.group(1) + json.dumps(embedded, ensure_ascii=False, separators=(",", ":")) + match.group(3)
    html = html[:match.start()] + replacement + html[match.end():]

    html = html.replace(
        "value.toLocaleString('es-PR', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' })",
        "value.toLocaleString('es-PR', { timeZone:'America/Puerto_Rico', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' })",
    )
    html = html.replace(
        "latest.toLocaleString()",
        "latest.toLocaleString('es-PR', { timeZone:'America/Puerto_Rico', dateStyle:'short', timeStyle:'medium' })",
    )
    INDEX_PATH.write_text(html, encoding="utf-8", newline="\n")
    print(f"Set dashboard time to Puerto Rico: {generated_at}")


if __name__ == "__main__":
    main()
