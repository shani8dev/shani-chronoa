"""Test that usb_devices skill includes usb-devices driver/class detail."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path("usr/lib/shani-chronoa")))
from shani_chronoa.skills import usb_devices as U

def test_usb_devices_includes_driver_class_detail():
    """Verify the skill output contains USB driver and class detail section."""
    output = U._run({})
    # Should contain the header we added
    assert "USB driver and class detail:" in output
    # Should show class information in parentheses
    assert "class (hub" in output or "class (" in output
    # Should still show the original USB device listing
    assert "USB device interface(s) attached:" in output

if __name__ == "__main__":
    test_usb_devices_includes_driver_class_detail()
    print("Test passed!")