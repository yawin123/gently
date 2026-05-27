"""
Demo interactivo: recorre los 10 formularios con el backend curses real.

Uso:
    python3 tests/demo_collect.py                # config vacío
    python3 tests/demo_collect.py config.toml    # precargar config existente

Al terminar imprime el config resultante en TOML por stdout.
No escribe ningún fichero.
"""
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

import tomli_w
from model.config import load_config, GentlyConfig
from ui.curses_backend import CursesBackend
from gently import collect


def main() -> None:
    if len(sys.argv) > 1:
        path = sys.argv[1]
        print(f"Cargando config desde {path!r}...")
        config = load_config(path)
    else:
        print("Iniciando con config vacío...")
        config = GentlyConfig()

    backend = CursesBackend()
    result = collect(config, backend)

    print("\n--- Config resultante (TOML) ---")
    print(tomli_w.dumps(result.to_dict()))


if __name__ == "__main__":
    main()
