import json
from pathlib import Path

DATA = Path(__file__).parent / "data"

def load(name):
    with open(DATA / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)