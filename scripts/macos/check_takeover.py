"""Explicit local physical-input check. No generated input, accounts, or model calls."""

import argparse
import json
import sys
import time

from jarvis.platforms.windows.ghost import CursorTarget, HumanTakeover
from jarvis.tools.windows import Rectangle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=15, choices=range(3, 61), metavar="3..60")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This live check requires macOS.")
    print(
        "Move near the cursor, click, or press/release a key; then leave input idle for 2 seconds.",
        flush=True,
    )
    monitor = HumanTakeover()
    result: dict[str, object] = {"platform": "darwin", "monitor_ready": False}
    try:
        monitor.start()
        initial = monitor.previous
        if initial is None:
            raise OSError("input_monitor_unavailable")
        result["monitor_ready"] = True
        monitor.target = CursorTarget(
            rectangle=Rectangle(
                left=initial.x - 1, top=initial.y - 1, right=initial.x + 1, bottom=initial.y + 1
            ),
            window_handle=initial.foreground,
        )
        yielded = quiet = False
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            monitor.sample()
            yielded = yielded or monitor.pending
            if monitor.quiet:
                quiet = True
                monitor.acknowledge()
            time.sleep(0.01)
        current = monitor.previous
        if current is not None:
            result.update(
                physical_mouse_events=current.mouse_serial - initial.mouse_serial,
                physical_key_events=current.key_serial - initial.key_serial,
                physical_moves=(current.move_serial or 0) - (initial.move_serial or 0),
                yield_observed=yielded,
                quiet_observed=quiet,
            )
        result["acceptance"] = "passed" if yielded and quiet else "requires_physical_input"
    except OSError:
        result["acceptance"] = "input_monitor_unavailable"
    finally:
        monitor.stop()
        result["monitor_stopped"] = not monitor.enabled
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("acceptance") == "passed" else 2)


if __name__ == "__main__":
    main()
