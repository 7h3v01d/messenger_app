"""
Windows taskbar unread-count overlay badge.

Qt6 dropped QtWinExtras (which used to wrap this), so this talks to the
Windows ITaskbarList3 COM interface directly via comtypes. No-ops
cleanly on non-Windows platforms.

Requirements:
    pip install comtypes pywin32
"""

import shutil
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import comtypes
    from comtypes import GUID, STDMETHOD, HRESULT
    from ctypes import POINTER, c_int, c_uint64, windll
    from ctypes.wintypes import HWND, HICON, LPCWSTR, BOOL, UINT, DWORD
    import win32gui
    import win32con

    CLSID_TaskbarList = GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}")

    class ITaskbarList3(comtypes.IUnknown):
        _iid_ = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
        _methods_ = [
            # ITaskbarList
            STDMETHOD(HRESULT, "HrInit"),
            STDMETHOD(HRESULT, "AddTab", [HWND]),
            STDMETHOD(HRESULT, "DeleteTab", [HWND]),
            STDMETHOD(HRESULT, "ActivateTab", [HWND]),
            STDMETHOD(HRESULT, "SetActiveAlt", [HWND]),
            # ITaskbarList2
            STDMETHOD(HRESULT, "MarkFullscreenWindow", [HWND, BOOL]),
            # ITaskbarList3
            STDMETHOD(HRESULT, "SetProgressValue", [HWND, c_uint64, c_uint64]),
            STDMETHOD(HRESULT, "SetProgressState", [HWND, c_int]),
            STDMETHOD(HRESULT, "RegisterTab", [HWND, HWND]),
            STDMETHOD(HRESULT, "UnregisterTab", [HWND]),
            STDMETHOD(HRESULT, "SetTabOrder", [HWND, HWND]),
            STDMETHOD(HRESULT, "SetTabActive", [HWND, HWND, DWORD]),
            STDMETHOD(HRESULT, "ThumbBarAddButtons", [HWND, UINT, c_int]),
            STDMETHOD(HRESULT, "ThumbBarUpdateButtons", [HWND, UINT, c_int]),
            STDMETHOD(HRESULT, "ThumbBarSetImageList", [HWND, c_int]),
            STDMETHOD(HRESULT, "SetOverlayIcon", [HWND, HICON, LPCWSTR]),
            STDMETHOD(HRESULT, "SetThumbnailTooltip", [HWND, LPCWSTR]),
            STDMETHOD(HRESULT, "SetThumbnailClip", [HWND, c_int]),
        ]


class TaskbarBadge:
    """Sets/clears the small overlay icon on the Windows taskbar button."""

    def __init__(self, hwnd: int):
        self._enabled = IS_WINDOWS
        self._hwnd = hwnd
        self._taskbar = None
        self._tmp_dir = None
        self._current_hicon = None

        if self._enabled:
            self._tmp_dir = Path(tempfile.mkdtemp(prefix="messenger_badge_"))
            try:
                self._taskbar = comtypes.CoCreateInstance(
                    CLSID_TaskbarList, interface=ITaskbarList3
                )
                self._taskbar.HrInit()
            except Exception:
                self._enabled = False

    def set_count(self, count: int):
        if not self._enabled or self._taskbar is None:
            return
        if count <= 0:
            self.clear()
            return

        from icon_assets import build_badge_icon

        icon_path = self._tmp_dir / "badge.ico"
        build_badge_icon(count).pixmap(64, 64).save(str(icon_path), "ICO")

        hicon = win32gui.LoadImage(
            0, str(icon_path), win32con.IMAGE_ICON, 0, 0, win32con.LR_LOADFROMFILE
        )
        try:
            self._taskbar.SetOverlayIcon(self._hwnd, hicon, f"{count} unread")
        finally:
            # SetOverlayIcon copies the icon internally; the caller
            # stays responsible for freeing the HICON it loaded, same
            # as any icon not loaded with LR_SHARED. Leaving this out
            # leaks a GDI handle on every unread-count update.
            self._destroy_previous_icon()
            self._current_hicon = hicon

    def clear(self):
        if not self._enabled or self._taskbar is None:
            return
        self._taskbar.SetOverlayIcon(self._hwnd, 0, "")
        self._destroy_previous_icon()

    def _destroy_previous_icon(self):
        if self._current_hicon:
            win32gui.DestroyIcon(self._current_hicon)
            self._current_hicon = None

    def cleanup(self):
        """Call on app shutdown: frees the last icon handle and removes
        the temp directory used to stage badge .ico files."""
        self._destroy_previous_icon()
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
