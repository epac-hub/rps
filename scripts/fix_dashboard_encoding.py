from pathlib import Path
import re


INDEX = Path(__file__).resolve().parents[1] / "index.html"


def repair_mojibake(text):
    def decode_pair(match):
        first, second = match.group(1), match.group(2)
        codepoint = ((ord(first) - 0xC0) << 6) + (ord(second) - 0x80)
        return chr(codepoint)

    return re.sub(r"([ÃÂ])([\x80-\xFF])", decode_pair, text)


def main():
    text = INDEX.read_text(encoding="utf-8")
    text = repair_mojibake(text)
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
