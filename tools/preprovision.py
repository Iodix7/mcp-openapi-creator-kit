"""azd's hook always previews reconciliation, irrespective of ambient apply flags."""
from pathlib import Path
import subprocess

from local_python import local_python


def main():
    root = Path(__file__).resolve().parent.parent
    python = local_python(root)
    for arguments in (
        ["tools/build-facade.py"],
        ["tools/build-policy-mcp.py", "--all", "--allow-incompatible"],
        ["tools/build-catalog.py"],
        ["tools/validate-deployment-profile.py", "--check-azure-resources"],
        ["tools/reconcile-all.py", "--skip-if-unprovisioned"],
    ):
        subprocess.run([python, *arguments], cwd=root, check=True)


if __name__ == "__main__":
    main()
