"""Explicit repository-local interpreter selection; never installs or falls back."""
from pathlib import Path
import os
import sys


def local_python(root: Path) -> str:
    if sys.version_info < (3, 12):
        raise RuntimeError("Python >=3.12 is required.")
    if __package__:
        if sys.prefix == sys.base_prefix or not (Path(sys.prefix) / "pyvenv.cfg").is_file():
            raise RuntimeError("Install the kit in a dedicated virtual environment and use "
                               "its absolute Python interpreter; global installs are unsupported.")
        return sys.executable
    environment = root / ".venv"
    if environment.is_symlink() or environment.is_junction():
        raise RuntimeError("The dedicated .venv must be a local directory, not a symlink/junction.")
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file() or not (environment / "pyvenv.cfg").is_file():
        raise RuntimeError("Missing repository .venv. Explicitly create it with Python >=3.12 "
                           "and install this repository with .venv's python -m pip install -e '.[dev]'.")
    if Path(sys.prefix).resolve() != environment.resolve():
        raise RuntimeError(f"Run this command using {interpreter}; global Python is not supported.")
    return str(interpreter)
