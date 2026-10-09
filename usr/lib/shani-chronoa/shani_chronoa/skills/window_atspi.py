"""Shared AT-SPI backend for window operations (focus, close).

Provides AT-SPI-based window control for Wayland sessions, with xdotool fallback for X11.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# A shared backend for focus_window and close_window, not a skill: without the
# marker every startup logged "Skipping 'builtin:window_atspi': SKILLS must be a
# list of Skill entries" (see skills/__init__.py NOT_A_SKILL).
_CHRONOA_NOT_A_SKILL = True


def _atspi():
    """Initialize and return ATSPi module or raise ImportError."""
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        return Atspi
    except (ImportError, ValueError) as exc:
        raise ImportError("AT-SPI library not available") from exc


def _get_windows_from_atspi() -> List[Tuple[str, str]]:
    """Get list of windows from AT-SPI bus as (app_name, window_title) tuples.
    
    Returns:
        List of (application_name, window_title) tuples for top-level windows.
    """
    try:
        Atspi = _atspi()
        desktop = Atspi.get_desktop(0)
        
        windows = []
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
                
            try:
                app_name = app.get_name() or ""
                if not app_name:
                    continue
                    
                # Look for top-level windows (role: frame, window, dialog)
                for j in range(app.get_child_count()):
                    child = app.get_child_at_index(j)
                    if child is None:
                        continue
                        
                    role = child.get_role_name()
                    if role in ("frame", "window", "dialog"):
                        title = child.get_name() or ""
                        if title:  # Only include windows with titles
                            windows.append((app_name, title))
                            
            except Exception:  # noqa: BLE001
                # Skip inaccessible apps
                continue
                
        return windows
        
    except Exception as exc:
        logger.debug("Failed to get windows from AT-SPI: %s", exc)
        return []


def focus_window_atspi(window_id: Optional[str] = None, title_contains: Optional[str] = None) -> str:
    """Focus a window using AT-SPI.
    
    Args:
        window_id: Window identifier (not used in AT-SPI version, kept for API compatibility)
        title_contains: Substring to match in window title
        
    Returns:
        Status message indicating success or failure reason.
    """
    try:
        Atspi = _atspi()
        windows = _get_windows_from_atspi()
        
        if not windows:
            return "No windows found on accessibility bus"
            
        # Filter windows if title_contains is provided
        if title_contains:
            filtered = [
                (app, title) for app, title in windows 
                if title_contains.lower() in title.lower()
            ]
            if not filtered:
                return f"No window title contains {title_contains!r}"
            windows = filtered
            
        if len(windows) > 1:
            # Ambiguous match - list options
            options = "\n".join(
                f"    {app}: {title[:60]}" for app, title in windows[:8]
            )
            conditional = title_contains if title_contains else "(any)"
            return (
                f"{len(windows)} windows match {conditional!r}, "
                f"so nothing was focused - guessing would focus the wrong one. "
                f"Narrow it, or pass a window_id:\n{options}"
            )
            
        # Focus the window
        app_name, title = windows[0]
        
        # Find the actual accessible object to focus
        desktop = Atspi.get_desktop(0)
        target_acc = None
        
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
                
            if app.get_name() == app_name:
                # Look for window with matching title
                for j in range(app.get_child_count()):
                    child = app.get_child_at_index(j)
                    if child is None:
                        continue
                        
                    role = child.get_role_name()
                    if role in ("frame", "window", "dialog"):
                        child_title = child.get_name() or ""
                        if child_title == title:
                            target_acc = child
                            break
                if target_acc:
                    break
                    
        if target_acc is None:
            return f"Could not locate window {title!r} from {app_name!r} in accessibility tree"
            
        # Try to focus using AT-SPI
        if hasattr(target_acc, "grab_focus"):
            if target_acc.grab_focus():
                return f"Focused window '{title}' from {app_name} via AT-SPI"
            else:
                return f"Failed to focus window '{title}' via AT-SPI (grab_focus returned False)"
        else:
            # Fallback: try Component interface
            try:
                comp = target_acc.get_component_iface()
                if comp and hasattr(comp, "grab_focus"):
                    if comp.grab_focus():
                        return f"Focused window '{title}' from {app_name} via AT-SPI Component"
                    else:
                        return f"Failed to focus window '{title}' via AT-SPI Component"
                else:
                    return f"Window '{title}' from {app_name} has no focus capability in AT-SPI"
            except Exception as exc:
                return f"Error accessing AT-SPI component for window '{title}': {exc}"
                
    except ImportError:
        return "AT-SPI not available for window focusing"
    except Exception as exc:
        logger.exception("Unexpected error in AT-SPI window focus")
        return f"Unexpected error focusing window via AT-SPI: {exc}"


def close_window_atspi(window_id: Optional[str] = None, title_contains: Optional[str] = None) -> str:
    """Close a window using AT-SPI.
    
    Args:
        window_id: Window identifier (not used in AT-SPI version, kept for API compatibility)
        title_contains: Substring to match in window title
        
    Returns:
        Status message indicating success or failure reason.
    """
    try:
        Atspi = _atspi()
        windows = _get_windows_from_atspi()
        
        if not windows:
            return "No windows found on accessibility bus"
            
        # Filter windows if title_contains is provided
        if title_contains:
            filtered = [
                (app, title) for app, title in windows 
                if title_contains.lower() in title.lower()
            ]
            if not filtered:
                return f"No window title contains {title_contains!r}"
            windows = filtered
            
        if len(windows) > 1:
            # Ambiguous match - list options
            options = "\n".join(
                f"    {app}: {title[:60]}" for app, title in windows[:8]
            )
            matched = title_contains if title_contains else "(any)"
            return (
                f"{len(windows)} windows match {matched!r}, "
                f"so nothing was closed - guessing would close the wrong one. "
                f"Narrow it, or pass a window_id:\n{options}"
            )
            
        # Close the window
        app_name, title = windows[0]
        
        # Find the actual accessible object to close
        desktop = Atspi.get_desktop(0)
        target_acc = None
        
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
                
            if app.get_name() == app_name:
                # Look for window with matching title
                for j in range(app.get_child_count()):
                    child = app.get_child_at_index(j)
                    if child is None:
                        continue
                        
                    role = child.get_role_name()
                    if role in ("frame", "window", "dialog"):
                        child_title = child.get_name() or ""
                        if child_title == title:
                            target_acc = child
                            break
                if target_acc:
                    break
                    
        if target_acc is None:
            return f"Could not locate window {title!r} from {app_name!r} in accessibility tree"
            
        # Try to close using AT-SPI actions
        # Common close action names: "close", "quit"
        action_names = ["close", "quit"]
        
        try:
            # Check if the accessible has an Action interface
            if hasattr(target_acc, "get_n_actions"):
                n_actions = target_acc.get_n_actions()
                for i in range(n_actions):
                    action_name = target_acc.get_action_name(i)
                    if action_name.lower() in action_names:
                        if target_acc.do_action(i):
                            return f"Closed window '{title}' from {app_name} via AT-SPI action '{action_name}'"
                        else:
                            return f"Failed to close window '{title}' via AT-SPI action '{action_name}' (action returned False)"
                            
            # If no specific close/quit action found, try to get the window's frame and look for actions there
            # Some window managers expose close actions on the frame rather than the window itself
            frame = target_acc
            while frame.get_parent() is not None:
                parent = frame.get_parent()
                if parent.get_role_name() == "application":
                    break
                frame = parent
                
            if frame != target_acc:  # We found a frame
                if hasattr(frame, "get_n_actions"):
                    n_actions = frame.get_n_actions()
                    for i in range(n_actions):
                        action_name = frame.get_action_name(i)
                        if action_name.lower() in action_names:
                            if frame.do_action(i):
                                return f"Closed window '{title}' from {app_name} via AT-SPI frame action '{action_name}'"
                            else:
                                return f"Failed to close window '{title}' via AT-SPI frame action '{action_name}' (action returned False)"
                                
        except Exception as exc:
            logger.debug("Error trying AT-SPI actions on window: %s", exc)
            
        return f"Window '{title}' from {app_name} has no close/quit action available via AT-SPI"
        
    except ImportError:
        return "AT-SPI not available for window closing"
    except Exception as exc:
        logger.exception("Unexpected error in AT-SPI window close")
        return f"Unexpected error closing window via AT-SPI: {exc}"


def is_atspi_available() -> bool:
    """Check if AT-SPI is available and functional.
    
    Returns:
        True if AT-SPI can be initialized and used, False otherwise.
    """
    try:
        Atspi = _atspi()
        # Try to get desktop to verify it works
        desktop = Atspi.get_desktop(0)
        _ = desktop.get_child_count()  # Just testing access
        return True
    except Exception:
        return False