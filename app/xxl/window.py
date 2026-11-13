"""Open the UI as a standalone app window (Edge --app), without heavy imports."""
import logging
import os
import subprocess

log = logging.getLogger(__name__)


def find_edge():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            path = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.isfile(path):
                return path
    return None


def open_app_window(url, fallback_dir):
    edge = find_edge()
    if edge:
        # Profile lives outside the app folder so the folder can be renamed/moved.
        base = os.environ.get("LOCALAPPDATA") or fallback_dir
        profile = os.path.join(base, "羞羞脸变声", "window")
        try:
            os.makedirs(profile, exist_ok=True)
            subprocess.Popen(
                [edge, "--app=" + url, "--user-data-dir=" + profile, "--no-first-run",
                 "--no-default-browser-check", "--disable-background-mode",
                 "--disable-features=msEdgeStartupBoost,Translate", "--window-size=1360,900"],
                close_fds=True,
                cwd=os.path.dirname(profile),  # never inside the app folder (keeps it renamable)
            )
            return True
        except Exception:
            log.exception("edge launch failed")
    import webbrowser

    return webbrowser.open(url)
