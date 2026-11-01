"""Check a packaged helper without development Python or a user's credentials."""

import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile


app = Path(sys.argv[1]).resolve()
helper = app / "Contents/Helpers/burnctl-engine"
home = tempfile.mkdtemp(prefix="burnctl-empty-home-", dir=app.parent)
environment = {"HOME": home, "PATH": "/usr/bin:/bin", "SSL_CERT_DIR": "/nonexistent"}


def report(*arguments):
    result = subprocess.run(
        [str(helper), *arguments], env=environment, cwd=home,
        capture_output=True, timeout=120, check=True,
    )
    value = json.loads(result.stdout)
    assert value["schema_version"] == 1
    assert not result.stderr, "Helper emitted unexpected diagnostics"
    return value


for period in ("current", "previous"):
    value = report("--demo", "--period", period)
    assert value["demo"] and value["period"] == period
    assert any(row["source_status"] == "ok" for row in value["agents"])
    print(f"PASS: self-contained synthetic {period} report")

empty = report("--period", "current")
assert not empty["demo"]
assert all(row["source_status"] != "ok" for row in empty["agents"])
assert all(row["period_cost"] is None for row in empty["agents"])
print("PASS: clean HOME produces unavailable sources, not invented zeroes")

certificates = app / "Contents/Resources/engine/_internal/certifi/cacert.pem"
assert certificates.is_file() and certificates.stat().st_size > 100_000
print("PASS: trust bundle is inside the app")

magic_numbers = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
}
count = 0
for path in (app / "Contents").rglob("*"):
    if not path.is_file() or path.is_symlink():
        continue
    with path.open("rb") as stream:
        if stream.read(4) not in magic_numbers:
            continue
    result = subprocess.run(["/usr/bin/otool", "-L", str(path)], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines()[1:]:
        dependency = line.strip().split(" (", 1)[0]
        assert not dependency.startswith(("/usr/local/", "/opt/homebrew/", "/Users/")), (path, dependency)
    count += 1
print(f"PASS: {count} Mach-O binaries have no external Homebrew/user load dependencies")

with (app / "Contents/Info.plist").open("rb") as stream:
    info = plistlib.load(stream)
print(f"Bundle minimum macOS: {info['LSMinimumSystemVersion']}")
print("Retained empty test HOME:", home)
