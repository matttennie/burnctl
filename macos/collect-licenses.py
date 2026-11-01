"""Copy the bundled runtime's installed license notices into the local app."""

import ast
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys
import sysconfig


destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
toc = ast.literal_eval(Path(sys.argv[2]).read_text())
prefixes = {Path(sys.base_prefix).resolve()}


def scan(value):
    if isinstance(value, (list, tuple)):
        for child in value:
            scan(child)
    elif isinstance(value, str) and value.startswith("/"):
        path = Path(value).resolve()
        if "Cellar" in path.parts:
            index = path.parts.index("Cellar")
            prefixes.add(Path(*path.parts[:index + 3]))


scan(toc)
copied = set()
for prefix in sorted(prefixes):
    for pattern in ("LICENSE*", "COPYING*", "COPYRIGHT*"):
        for source in prefix.glob(pattern):
            if source.is_file() and source not in copied:
                target = destination / f"{prefix.parent.name}-{prefix.name}-{source.name}"
                shutil.copyfile(source, target)
                copied.add(source)
pyinstaller = distribution("PyInstaller")
for name in ("PyInstaller", "certifi"):
    package = distribution(name)
    for path in package.files or []:
        if "/licenses/" in str(path) and Path(path).name.startswith(("COPYING", "LICENSE")):
            shutil.copyfile(package.locate_file(path), destination / f"{name}-{Path(path).name}")

(destination / "Runtime.txt").write_text(
    f"Bundled Python: {sys.version}\n"
    f"Python deployment target: {sysconfig.get_config_var('MACOSX_DEPLOYMENT_TARGET')}\n"
    f"PyInstaller: {pyinstaller.version}\n"
    f"certifi: {distribution('certifi').version}\n"
    "This is a local development build, not a published distribution.\n"
)
