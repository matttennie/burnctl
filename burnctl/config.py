"""Persistent configuration for burnctl."""

import json
import os
import sys

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "burnctl")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
_MAX_CONFIG_BYTES = 1 * 1024 * 1024  # 1 MiB

DEFAULTS = {
    "billing_day": 1,
    "billing_interval": "mo",
    "default_agents": "all",
    "theme": "gradient",
    "no_color": False,
    "simple": False,
    "compact": False,
    "claude_plan": "free",
    "claude_billing_day": 0,
    "gemini_plan": "free",
    "gemini_billing_day": 0,
    "codex_plan": "free",
    "codex_billing_day": 0,
    "agent_plans": {},
    "agent_billing_days": {},
}

PUBLIC_GLOBAL_KEYS = (
    "billing_day",
    "billing_interval",
    "default_agents",
    "theme",
    "no_color",
    "simple",
    "compact",
)

# Theme options: gradient (24-bit gradient), classic (16-color), colorblind, accessible
THEMES = ("gradient", "classic", "colorblind", "accessible")

PLAN_PRICES = {
    "free": 0,
    "pro": 20,
    "max5x": 100,
    "max20x": 200,
}

GEMINI_PLAN_PRICES = {
    "free": 0,
    "ai_plus": 7.99,
    "ai_pro": 19.99,
    "ai_ultra": 249.99,
}

CODEX_PLAN_PRICES = {
    "free": 0,
    "go": 8,
    "plus": 20,
    "pro": 200,
}

# Pro is the only plan with annual option ($200/yr = $16.67/mo effective)
ANNUAL_PRICES = {
    "pro": 200,
}

# Normalize user input to canonical interval
_INTERVAL_ALIASES = {
    "mo": "mo", "month": "mo", "monthly": "mo",
    "yr": "yr", "year": "yr", "yearly": "yr", "annual": "yr", "annually": "yr",
}


def _valid_agent_billing_day(v):
    return v == 0 or 1 <= v <= 31


# Validation rules: key -> (validator_fn, error_message)
_VALIDATORS = {
    "billing_day": (lambda v: 1 <= v <= 31, "must be between 1 and 31"),
    "claude_billing_day": (
        _valid_agent_billing_day, "must be 0 (use global) or 1-31",
    ),
    "gemini_billing_day": (
        _valid_agent_billing_day, "must be 0 (use global) or 1-31",
    ),
    "codex_billing_day": (
        _valid_agent_billing_day, "must be 0 (use global) or 1-31",
    ),
    "claude_plan": (
        lambda v: v in PLAN_PRICES,
        f"must be one of: {', '.join(PLAN_PRICES.keys())}",
    ),
    "gemini_plan": (
        lambda v: v in GEMINI_PLAN_PRICES,
        f"must be one of: {', '.join(GEMINI_PLAN_PRICES.keys())}",
    ),
    "codex_plan": (
        lambda v: v in CODEX_PLAN_PRICES,
        f"must be one of: {', '.join(CODEX_PLAN_PRICES.keys())}",
    ),
    "billing_interval": (
        lambda v: v in _INTERVAL_ALIASES,
        f"must be one of: {', '.join(sorted(set(_INTERVAL_ALIASES.keys())))}",
    ),
    "theme": (lambda v: v in THEMES, f"must be one of: {', '.join(THEMES)}"),
    "default_agents": (
        lambda v: isinstance(v, str) and len(v) > 0,
        "must be a non-empty string (e.g., 'all', 'claude', 'claude,gemini')",
    ),
}

_SCOPED_AGENTS = (
    "claude", "gemini", "codex", "openrouter",
    "local", "opencode",
)

_SCOPED_KEYS = ("billing_plan", "billing_day")

_SCOPED_PLAN_VALIDATORS = {
    "claude": (
        lambda v: v in PLAN_PRICES,
        f"must be one of: {', '.join(PLAN_PRICES.keys())}",
    ),
    "gemini": (
        lambda v: v in GEMINI_PLAN_PRICES,
        f"must be one of: {', '.join(GEMINI_PLAN_PRICES.keys())}",
    ),
    "codex": (
        lambda v: v in CODEX_PLAN_PRICES,
        f"must be one of: {', '.join(CODEX_PLAN_PRICES.keys())}",
    ),
}


def effective_price(plan, interval):
    """Get the effective monthly price for a *plan* + *interval* combo."""
    if interval == "yr" and plan in ANNUAL_PRICES:
        return ANNUAL_PRICES[plan] / 12
    # Annual interval with no annual pricing falls back to monthly rate
    return PLAN_PRICES.get(plan, 0)


def _first_run_hint():
    """Print a one-time setup hint when no config file exists yet."""
    print(
        "\n"
        "  Welcome to burnctl!  A few settings make the report accurate:\n"
        "\n"
        "    burnctl config billing_day  <1-31>   "
        "  # default billing day for all agents\n"
        "    burnctl config --codex billing_plan pro billing_day 18\n"
        "    burnctl config --claude billing_plan pro\n"
        "    burnctl config --gemini billing_plan ai_pro\n"
        "    burnctl config --openrouter billing_plan enterprise billing_day 10\n"
        "\n"
        "  Run `burnctl config` to see all options.\n",
        file=sys.stderr,
    )


def load():
    """Load config, merging saved values over defaults."""
    config = dict(DEFAULTS)
    first_run = not os.path.isfile(CONFIG_FILE)
    if not first_run:
        try:
            size = os.path.getsize(CONFIG_FILE)
            if size > _MAX_CONFIG_BYTES:
                print(
                    f"Warning: config file too large ({size:,} bytes), "
                    "using defaults.",
                    file=sys.stderr,
                )
                return config
            with open(CONFIG_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            # Only merge known keys — reject arbitrary injected keys
            for key in DEFAULTS:
                if key not in saved:
                    continue
                if isinstance(DEFAULTS[key], dict) and isinstance(saved[key], dict):
                    merged = dict(DEFAULTS[key])
                    merged.update(saved[key])
                    config[key] = merged
                else:
                    config[key] = saved[key]
        except json.JSONDecodeError:
            print(
                "Warning: config file is malformed, using defaults.",
                file=sys.stderr,
            )
            print(f"  Fix or delete: {CONFIG_FILE}", file=sys.stderr)
        except OSError as exc:
            print(f"Warning: could not read config: {exc}", file=sys.stderr)

    if first_run:
        _first_run_hint()

    return config


def save(config):
    """Save *config* to disk."""
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
    except OSError as exc:
        print(f"Error: could not save config: {exc}", file=sys.stderr)
        print(f"  Check permissions on: {CONFIG_DIR}", file=sys.stderr)
        sys.exit(1)


def show():
    """Print current configuration to stdout."""
    config = load()
    print(f"Config file: {CONFIG_FILE}")
    print()
    for key in PUBLIC_GLOBAL_KEYS:
        val = config[key]
        default = DEFAULTS.get(key)
        marker = "" if val == default else "  (modified)"
        print(f"  {key}: {val}{marker}")
    scoped_agents = sorted(
        set(config.get("agent_plans", {}).keys())
        | set(config.get("agent_billing_days", {}).keys())
    )
    if scoped_agents:
        print()
        print("Scoped agent settings:")
        for agent in scoped_agents:
            plan = config.get("agent_plans", {}).get(agent, "")
            billing_day = config.get("agent_billing_days", {}).get(agent, 0)
            if plan:
                print(f"  {agent}.billing_plan: {plan}")
            if billing_day:
                print(f"  {agent}.billing_day: {billing_day}")
    print()
    print("Set global values with: burnctl config <key> <value> [<key> <value> ...]")
    print("  e.g.: burnctl config billing_day 15 theme colorblind")
    print("Set agent values with: burnctl config --<agent> billing_plan <plan> billing_day <day>")
    print("  e.g.: burnctl config --codex billing_plan pro billing_day 18")
    print("        burnctl config --gemini billing_plan ai_pro")


def _coerce_plain_value(key, value):
    expected_type = type(DEFAULTS[key])

    if expected_type is bool:
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
            return False
        print(
            f"Error: '{key}' must be true/false (or yes/no, 1/0).",
            file=sys.stderr,
        )
        sys.exit(1)

    if expected_type is int:
        try:
            return int(value)
        except ValueError:
            print(f"Error: '{key}' must be an integer.", file=sys.stderr)
            sys.exit(1)

    if key == "billing_interval":
        return _INTERVAL_ALIASES.get(value.lower(), value.lower())

    return value


def _validate_plain_value(key, value, config):
    validator, err_msg = _VALIDATORS.get(key, (None, None))
    if validator and not validator(value):
        print(f"Error: {key} {err_msg}.", file=sys.stderr)
        sys.exit(1)

    if key == "billing_interval" and value == "yr":
        plan = config.get("claude_plan", "free")
        if plan not in ANNUAL_PRICES:
            print(
                f"Note: {plan} plan doesn't have annual pricing. "
                "Using monthly rate.",
                file=sys.stderr,
            )
    if key == "claude_plan" and config.get("billing_interval") == "yr" and value not in ANNUAL_PRICES:
        print(
            f"Note: {value} plan doesn't have annual pricing. "
            "Using monthly rate.",
            file=sys.stderr,
        )


def set_values(pairs):
    """Set one or more global config key/value pairs."""
    config = load()
    for key, raw_value in pairs:
        if key not in PUBLIC_GLOBAL_KEYS:
            print(f"Error: unknown config key '{key}'", file=sys.stderr)
            print(
                f"Valid keys: {', '.join(PUBLIC_GLOBAL_KEYS)}",
                file=sys.stderr,
            )
            sys.exit(1)
        value = _coerce_plain_value(key, raw_value)
        _validate_plain_value(key, value, config)
        config[key] = value
        print(f"Set {key} = {value}")
    save(config)


def _validate_scoped_plan(agent, value):
    validator, err_msg = _SCOPED_PLAN_VALIDATORS.get(
        agent, (lambda v: isinstance(v, str) and len(v) > 0, "must be a non-empty string")
    )
    if not validator(value):
        print(f"Error: {agent} billing_plan {err_msg}.", file=sys.stderr)
        sys.exit(1)


def set_scoped_values(agent, pairs):
    """Set one or more agent-scoped config key/value pairs."""
    if agent not in _SCOPED_AGENTS:
        print(f"Error: unknown agent '{agent}'", file=sys.stderr)
        sys.exit(1)

    config = load()
    config.setdefault("agent_plans", {})
    config.setdefault("agent_billing_days", {})

    for key, raw_value in pairs:
        if key not in _SCOPED_KEYS:
            valid = ", ".join(_SCOPED_KEYS)
            print(
                f"Error: unknown scoped key '{key}'. Valid scoped keys: {valid}",
                file=sys.stderr,
            )
            sys.exit(1)

        if key == "billing_plan":
            _validate_scoped_plan(agent, raw_value)
            config["agent_plans"][agent] = raw_value
            print(f"Set {agent}.billing_plan = {raw_value}")
        else:
            try:
                value = int(raw_value)
            except ValueError:
                print(f"Error: '{key}' must be an integer.", file=sys.stderr)
                sys.exit(1)
            if not _valid_agent_billing_day(value):
                print(f"Error: {key} must be 0 (use global) or 1-31.", file=sys.stderr)
                sys.exit(1)
            config["agent_billing_days"][agent] = value
            print(f"Set {agent}.billing_day = {value}")

    save(config)


def get_scoped_value(agent, key):
    """Read one agent-scoped config value."""
    if agent not in _SCOPED_AGENTS:
        print(f"Error: unknown agent '{agent}'", file=sys.stderr)
        sys.exit(1)
    if key not in _SCOPED_KEYS:
        valid = ", ".join(_SCOPED_KEYS)
        print(
            f"Error: unknown scoped key '{key}'. Valid scoped keys: {valid}",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load()
    if key == "billing_plan":
        value = config.get("agent_plans", {}).get(agent, "")
        if not value:
            fallback = config.get(f"{agent}_plan", "")
            value = fallback
    else:
        value = config.get("agent_billing_days", {}).get(agent, 0)
        if not value:
            fallback = config.get(f"{agent}_billing_day", 0)
            value = fallback if fallback else config.get("billing_day", 1)
    print(f"{agent}.{key}: {value}")


def set_value(key, value):
    """Set a single config *key* to *value* (with validation)."""
    set_values([(key, value)])
