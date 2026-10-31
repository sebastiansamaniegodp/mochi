# Los tests importan la app (src/) y el agente (src/agente, módulos de primer nivel).
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
for _path in (_SRC / "agente", _SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
