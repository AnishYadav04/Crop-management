"""Upload and start main.py after starting Wokwi in VS Code.

Run with the project virtual environment; install mpremote==1.29.0 first.
Wokwi's simulated filesystem is cleared when the simulation stops.
"""
from pathlib import Path
import subprocess
import sys


def main():
    firmware = Path(__file__).resolve().parents[1] / "esp32" / "main.py"
    base = [sys.executable, "-m", "mpremote", "connect", "port:rfc2217://localhost:4000"]
    try:
        subprocess.run(base + ["fs", "cp", str(firmware), ":main.py"], check=True, timeout=60)
        subprocess.run(base + ["exec", "--no-follow", "exec(open('main.py').read())"],
                       check=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("Upload/start failed. Start Wokwi, wait for MicroPython to boot, then retry.", file=sys.stderr)
        return 1
    print("Program started. Check the Wokwi serial output and dashboard device status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
