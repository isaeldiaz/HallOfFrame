"""Test the USB-relay horn directly.

Verifies the relay is reachable and switchable (port opens, writes succeed)
and optionally sounds the horn for the configured duration. Use this to confirm
the relay is present, switching, and wired to the horn before relying on it
during a race.

Usage:
    ./venv/bin/python -m hallofframe.tools.test_horn [CONFIG.toml]
    ./venv/bin/python -m hallofframe.tools.test_horn ~/kjørbo-regatta-2026/config.toml --beep
"""
from __future__ import annotations

import argparse
import sys
import time


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default=None,
                        help="path to config.toml (default: ~/regatta-data/config.toml)")
    parser.add_argument("--beep", action="store_true",
                        help="also sound the horn for the configured duration")
    args = parser.parse_args(argv)

    from ..config import ConfigError, load_config
    from ..horn import Horn

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    horn = Horn(config)
    ok, detail = horn.test_result
    print(f"horn enabled: {horn.enabled}  device: {horn.device}  "
          f"relay: {horn.relay}  duration_ms: {horn.duration_s * 1000:.0f}")
    print(f"self-test: {'OK' if ok else 'FAILED'}")
    print(f"  detail: {detail}")
    if not horn.enabled:
        print("(horn is disabled in config; enable [horn] to use it)",
              file=sys.stderr)

    if args.beep and horn.enabled:
        print(f"sounding horn for {horn.duration_s * 1000:.0f} ms...")
        horn.honk()
        time.sleep(horn.duration_s + 0.2)
    horn.stop()

    if horn.enabled and ok:
        # The LC-1 board gives no electronic feedback; the audible click/beep is
        # the real proof. Tell the operator what to listen for.
        print("The relay was toggled on and off. If you heard a click or beep,")
        print("the horn is working. If not, check the relay wiring and the 12 V")
        print("supply to the horn.")

    return 0 if (ok or not horn.enabled) else 1


if __name__ == "__main__":
    sys.exit(main())
