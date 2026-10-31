# Contributing

## Setup

Requires Python 3.10 or later.

```bash
git clone https://github.com/matttennie/burnctl.git
cd burnctl
python -m pip install -e . && pip install pytest flake8 mypy pre-commit && pre-commit install
```

## Testing

```bash
pytest tests -q
flake8 burnctl/ tests/
mypy burnctl/
```

CI runs these on Python 3.10-3.13 across Linux, macOS, and Windows, plus a build and `twine check`.

To try a branch end to end against your own data without touching your global install:

```bash
python3 -m venv /tmp/burnctl-test && source /tmp/burnctl-test/bin/activate
pip install . && hash -r   # hash -r: zsh caches the path of a previously installed burnctl
burnctl --version && burnctl
deactivate && rm -rf /tmp/burnctl-test
```

## Submitting changes

1. Create a branch from `main`.
2. Make your changes and add tests where applicable.
3. Run the three commands above and confirm everything passes.
4. Open a pull request against `main` with a clear description of what changed and why.

## Code style

- Python 3.10+, standard library only. Do not add runtime dependencies.
- 4-space indentation, LF line endings.
- flake8 with max line length 120 (see `.flake8`).

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.
