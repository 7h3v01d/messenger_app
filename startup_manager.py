"""
Windows "launch at startup" toggle, via the classic per-user Run
registry key (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run).
No-ops cleanly on non-Windows platforms.
"""

import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

APP_NAME = "MessengerStandalone"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

if IS_WINDOWS:
    import winreg


def _launch_command(entry_script: Path) -> str:
    """The command to register: prefers pythonw.exe (no console window)
    if it exists alongside the current interpreter, falls back to the
    interpreter currently running this script.

    entry_script must be the actual application entry point
    (messenger_app.py), passed in explicitly by the caller — deriving
    it from this module's own __file__ would register a command that
    launches startup_manager.py itself and immediately exits without
    ever starting the app.
    """
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe

    script = entry_script.resolve()
    return f'"{interpreter}" "{script}"'


def is_startup_enabled(entry_script: Path) -> bool:
    """True if a Run entry exists for this app matching the current
    launch command (so a moved/renamed install doesn't show as enabled)."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value == _launch_command(entry_script)
    except FileNotFoundError:
        return False


def set_startup_enabled(enabled: bool, entry_script: Path) -> None:
    if not IS_WINDOWS:
        return
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, APP_NAME, 0, winreg.REG_SZ, _launch_command(entry_script)
            )
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass

