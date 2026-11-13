# 羞羞脸变声 — entry point. Run with the bundled runtime:
#   runtime\pythonw.exe app\main.py            (normal, no console)
#   runtime\python.exe  app\main.py --console  (debug, logs in the console)
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
# The embedded runtime's python39._pth runs in isolated mode, so the script
# folder is not on sys.path by itself.
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

if __name__ == "__main__":
    from xxl.main import run

    sys.exit(run(ROOT))
