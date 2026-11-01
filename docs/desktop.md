# Burnctl for macOS — local preview

A native SwiftUI dashboard with an AppKit-owned window lifecycle, reading the
existing burnctl collectors. It shows usage,
source availability, and model detail without account-management actions. No
proxy installation, shell changes, credential editing, or background service is
part of the desktop app. The existing CLI remains unchanged.

## One repository, two interfaces

The desktop app stays in the same repository as the CLI. Both use the Python
collectors and reporting engine; the desktop does not fork that logic or require
a separate private repository.

- `burnctl/collectors/`, `burnctl/report.py`, and `burnctl/pricing.py`: shared
  collection, aggregation, and pricing logic.
- `burnctl/cli.py`: the existing terminal interface.
- `burnctl/desktop.py`: the read-only, versioned JSON adapter for the app.
- `macos/Sources/Burnctl/`: native window, SwiftUI interface, and helper client.
- `macos/build-app.sh`: bundles the native interface and the shared Python engine
  into a self-contained local app.

Collector fixes belong in the shared engine so both interfaces benefit. Keep
desktop interface and packaging changes alongside the CLI here.

## Open the app

The built app is `dist/Burnctl.app`. Double-click it in Finder, or run:

```sh
open dist/Burnctl.app
```

The app contains its Python runtime. You do **not** need Python, Homebrew, or a
checkout to run the built app. It can be copied to another folder, including a
path containing spaces. This preview is built for **Intel macOS 26** and tested
on this Mac only; it is not a universal or notarized release. The Swift interface
targets macOS 15, but the bundled Homebrew Python runtime requires macOS 26.
The local build is ad-hoc signed, not signed with Developer ID or published.

## What to review

1. Open **Overview** and compare the available sources with the CLI.
2. Select a provider to inspect its date range, usage, and model breakdown.
3. Switch between **Current** and **Previous**. Date ranges are per provider:
   subscriptions use configured billing periods, API sources may use rolling
   windows, and some quota APIs cannot provide historical cycles.
4. Press **Command-R** or Refresh. The timestamp advances when collection
   completes. A failed refresh should retain the previous successful reading
   with a clear error, rather than replacing it with zeroes.
5. Try light and dark appearance, resize the window, and select/copy a value.

For synthetic data that does not read your accounts or sessions, quit the app
and launch it in demo mode:

```sh
open -n dist/Burnctl.app --args --demo
```

Demo mode is explicitly marked. Quit that instance and open normally to return
to live data. Demo readings are for interface review, not billing evidence.

## Interpreting the readings

- Subscription token valuations are **estimated API-equivalent usage**, not
  your subscription bill, remaining quota, or money owed.
- Provider-reported costs and locally recorded costs retain their provenance.
  Recorded costs are not a verified invoice.
- Characters and credits are not tokens or dollars and are displayed separately.
- A missing credential, unreadable source, unsupported historical period, or
  collection failure is not represented as a zero balance.
- Totals may cover different provider date ranges; inspect each provider's dates
  before comparing amounts.
- The desktop adapter reuses existing collector behavior and pricing tables.
  It does not establish that upstream provider records are complete.

## Existing configuration and privacy

The reader uses `~/.config/burnctl/config.json`, existing session directories and
local usage ledgers, and credentials already supported by burnctl collectors.
It does not write configuration, credentials, or pricing history. Pricing is
cached in memory during collection, and existing saved history may be read.
Collectors may make read-only provider/pricing API requests. There is no
analytics endpoint or new hosted service.

HTTPS uses the bundled certifi CA certificates rather than a Homebrew-installed
OpenSSL trust directory. An explicit `SSL_CERT_FILE` supplied by the launching
environment is respected for custom trust configurations.

Finder-launched apps do not generally receive terminal-only environment
variables. Providers needing those variables may be unavailable when opened
from Finder. This first preview intentionally does not import your shell profile
or add a credential-management screen. Local session collectors still work.
To use terminal-exported API variables for testing, quit the app and launch its
executable directly from that terminal:

```sh
dist/Burnctl.app/Contents/MacOS/Burnctl
```

Raw provider errors are not shown because they can contain sensitive values.
The app only displays safe status messages. No private account/session data is
bundled into the app; demo data is synthetic.

## Build locally

Requires Xcode 16 or newer (macOS 15 SDK or newer) and Python 3.14 for the build only:

```sh
bash macos/build-app.sh
```

Override the Python executable with `BURNCTL_BUILD_PYTHON` if needed before the
first build. The script creates `.venv-macos`, installs the pinned PyInstaller
build dependencies, builds Swift, freezes the Python helper, copies runtime
license notices, creates the icon, and ad-hoc signs the app. No upload, signing
account, notarization, installer, or publishing step runs. Subsequent builds
preserve the previous app in their unique `build/macos/build.*` staging folder
instead of deleting it. Build and staging artifacts are ignored by git.

```sh
swift test --package-path macos --disable-sandbox
python3 -m pytest tests/test_desktop.py
python3 macos/smoke-test.py dist/Burnctl.app
python3 -m unittest discover -s macos -p 'test_packaging.py'
```

The app invokes `Contents/Helpers/burnctl-engine` directly, without a shell or
local HTTP server. Both subprocess output pipes are drained; cancellation and
timeouts only stop that app-owned helper. The JSON contract is versioned in
`burnctl/desktop.py`; `macos/Fixtures/report.json` contains its synthetic sample.
The smoke test uses an empty HOME and stripped PATH, checks missing-source
semantics, confirms bundled TLS certificates, and inspects the native binaries
for external Homebrew/user load dependencies. Its temporary HOME is retained
alongside the app instead of automatically deleted.

PyInstaller packaging reference:
[Using PyInstaller](https://www.pyinstaller.org/en/stable/usage.html).

## Verification — November 1, 2026

Verified locally on Intel macOS 26.6.2:

- 304 focused Python adapter, pricing, report, and integration tests passed.
- 15 Swift decoding, formatting, refresh-state, and actual subprocess tests passed.
- 3 packaged trust-store bootstrap regressions passed.
- Frozen current/previous demo reports and clean-HOME missing-source semantics
  passed without development Python or PYTHONPATH; frozen demo matched source.
- All 47 bundled Mach-O binaries had no external Homebrew/user load dependencies.
- Live provider collection worked with Homebrew's certificate directory disabled,
  using the bundled CA file. No raw credentials or usage records are included here.
- Relocation to a path containing spaces, deep signature verification, and
  Finder-style startup of the final release bundle passed.
- Light overview, dark provider details/controls, and unsupported-history states
  were visually reviewed. Closing during live collection stopped the app-owned
  helper; repeated launches created the expected window.

Only this Mac has been tested. App Store distribution, Developer ID signing,
notarization, universal binaries, and other macOS versions remain out of scope.
