"""Containment checks shared by read-only surfaces and explicit writers."""
from pathlib import Path


def workspace_root(root: Path) -> Path:
    root = root.expanduser()
    if not root.is_dir():
        raise ValueError("Workspace must be an existing directory; create it explicitly first.")
    if root.is_symlink() or root.is_junction():
        raise ValueError("Workspace root must not be a symlink/junction.")
    return root.resolve()


def safe_data_path(root: Path, path: Path) -> Path:
    root = root.resolve()
    candidate = path.absolute()
    if not candidate.is_relative_to(root):
        raise ValueError("Data path escapes workspace")
    for part in [candidate, *candidate.parents]:
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError("Symlink/junction data paths are not supported")
        if part != candidate and part.exists() and not part.is_dir():
            raise ValueError("Data path parent must be a directory")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Data path escapes workspace")
    if candidate.is_file() and candidate.stat().st_nlink != 1:
        raise ValueError("Hard-linked data paths are not supported")
    return candidate


def validate_data_tree(root: Path):
    for name in ("clients", "apis", "docs", "catalog", "infra"):
        directory = safe_data_path(root, root / name)
        for path in directory.rglob("*"):
            safe_data_path(root, path)


def module_outputs(client_dir: Path) -> dict[Path, bytes]:
    from .assets import kit_root
    root = client_dir.parent.parent
    directory = safe_data_path(root, client_dir / "generated" / "kit-modules")
    outputs = {}
    for source in sorted((kit_root() / "modules").glob("*")):
        if source.is_file():
            safe_data_path(kit_root(), source)
            target = safe_data_path(root, directory / source.name)
            outputs[target] = source.read_bytes()
    return outputs


def preflight_outputs(root: Path, directory: Path, paths):
    """Check the entire write/delete set before any output is changed."""
    safe_data_path(root, directory)
    for path in paths:
        safe_data_path(root, path)
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Generated output escapes its output directory")
        if path.exists() and not path.is_file():
            raise ValueError(f"Generated output is not a file: {path.name}")


def stage_modules(client_dir: Path):
    outputs = module_outputs(client_dir)
    preflight_outputs(client_dir.parent.parent, client_dir / "generated", outputs)
    for target, content in outputs.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
