"""
Demo: run installation phases from a configuration file.

Uso:
    python3 demos/demo_install.py demos/vm_config.toml                # local, dry-run
    python3 demos/demo_install.py demos/vm_config.toml --no-dry-run   # local, real
    python3 demos/demo_install.py demos/vm_config.toml --target ssh:root@192.168.30.81 --password toor

Si no se pasa --no-dry-run, se ejecuta en modo dry-run, mostrando solo
los comandos que se ejecutarían sin tocar nada.
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "vendor"))

from model.config import load_config
from model.validators import validate_coherence
from installer.runner import build_runner
from ui.curses_backend import CursesBackend


def main() -> None:
    parser = argparse.ArgumentParser(description="Gently — demo installer")
    parser.add_argument("--config", help="Path to config.toml")
    parser.add_argument(
        "--target", default="local",
        help="Target: 'local' or 'ssh:user@host' (default: local)",
    )
    parser.add_argument("--password", default=None, help="SSH password for remote target")
    parser.add_argument(
        "--no-dry-run", action="store_true", default=False,
        help="Execute real commands instead of dry-run",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    errors = validate_coherence(config)
    if errors:
        print("Configuration coherence errors:")
        for error_msg in errors:
            print(f"  - {error_msg}")
        sys.exit(1)

    dry_run = not args.no_dry_run
    runner = build_runner(
        args.target,
        dry_run=dry_run,
        ssh_password=args.password,
    )

    print(f"Target: {runner.transport}  dry_run={dry_run}")
    print(f"Config : {args.config}")
    print("-" * 40)
    print("Iniciando instalación — abriendo interfaz…")

    backend = CursesBackend()

    try:
        # run_install() arranca la instalación en un hilo de fondo y
        # gestiona la UI curses en el hilo principal, evitando conflictos
        # de terminal entre curses y los subprocesos de instalación.
        report = backend.run_install(config, runner)
    except KeyboardInterrupt:
        print("\nInstalación interrumpida por el usuario.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)

    # The backend's install_progress_end joined the UI thread, so curses is
    # already closed here.  Safe to print.
    print(f"Installation {'OK' if report.ok else 'FAILED'}")
    for phase in report.phases:
        status = "✓" if phase.status == "ok" else "✗"
        print(f"  {status} {phase.key}  ({phase.duration_sec:.1f}s)")
        if phase.error:
            print(f"       error: {phase.error}")


if __name__ == "__main__":
    main()
