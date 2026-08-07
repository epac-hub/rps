from pathlib import Path
import re


INDEX = Path(__file__).resolve().parents[1] / "index.html"


def repair_mojibake(text):
    # Repair text that was decoded once as Latin-1 instead of UTF-8.
    def decode_chunk(match):
        raw = bytes(ord(char) for char in match.group(0))
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return match.group(0)

    text = re.sub(r"(?:[ÃÂ][\x80-\xFF])+", decode_chunk, text)
    replacements = {
        "vehÃ\u00adculos": "vehículos",
        "vehÃ\u00adculo": "vehículo",
        "crÃ\u00adticas": "críticas",
        "operaciÃ\u00b3n": "operación",
        "seÃ\u00b1al": "señal",
        "desvÃ\u00ados": "desvíos",
        "trÃ\u00a1fico": "tráfico",
        "baterÃ\u00ada": "batería",
        "mÃ\u00a1s": "más",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def main():
    text = repair_mojibake(INDEX.read_text(encoding="utf-8"))
    replacements = {
        '<button onclick="showLayer(\'routes\')">Rutas</button>':
            '<button onclick="showLayer(\'routes\'); jumpTo(\'mapPanel\')">Rutas</button>',
        '<button onclick="showLayer(\'stops\')">Paradas</button>':
            '<button onclick="showLayer(\'stops\'); jumpTo(\'mapPanel\')">Paradas</button>',
        '<button onclick="showLayer(\'speeding\')">Velocidad</button>':
            '<button onclick="showLayer(\'speeding\'); jumpTo(\'mapPanel\')">Velocidad</button>',
        '<button onclick="showLayer(\'heat\')">Heatmap</button>':
            '<button onclick="showLayer(\'heat\'); jumpTo(\'mapPanel\')">Heatmap</button>',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    INDEX.write_text(text, encoding="utf-8", newline="\n")
    print("Repaired dashboard encoding and navigation buttons.")


if __name__ == "__main__":
    main()
