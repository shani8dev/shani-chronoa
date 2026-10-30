#!/usr/bin/env python3
"""shani-chronoa-doctor — self-check the tools shani-chronoa needs.

For every feature, lists the external binaries it calls, marks each
present/missing, and prints the distro install command for anything missing.

Modelled on `voltkit doctor` and `shani-doctor`.

Exit 0 if every needed tool is present, 1 otherwise.
"""
from __future__ import annotations

import os
import shutil
import sys

FEATURES = [
    ("Speech (STT)", ["whisper-cli", "pw-record", "pw-play"],
     "whisper-cpp pipewire", "whisper-cpp pipewire"),
    ("Speech (TTS)", ["espeak-ng", "piper-tts", "sox"],
     "espeak-ng piper sox", "espeak-ng piper sox"),
    ("Local LLM", ["llama-server", "llama-cli"],
     "llama-cpp", "llama-cpp"),
    ("Cloud LLM", ["curl"],
     "curl", "curl"),
    ("Audio", ["wpctl", "pactl", "pw-dump", "pw-cat"],
     "wireplumber pulseaudio-utils pipewire", "wireplumber pulseaudio-utils pipewire"),
    ("Bluetooth", ["bluetoothctl", "busctl", "gatttool", "kdeconnect-cli"],
     "bluez-utils dbus bluez", "bluez-utils dbus bluez"),
    ("Network", ["ip", "nmcli", "ping", "nmap", "mtr"],
     "iproute2 networkmanager iputils nmap mtr", "iproute2 network-manager iputils nmap mtr"),
    ("Bluetooth LE / phones", ["boltctl", "kcmshell6", "kscreen-doctor"],
     "bolt kscreen kde-cli-tools", "bolt kscreen kde-cli-tools"),
    ("System info", ["lscpu", "lsblk", "lspci", "lsusb", "df", "du", "ps", "uptime"],
     "util-linux pciutils usbutils procps-ng", "util-linux pciutils usbutils procps-ng"),
    ("Disk & storage", ["smartctl", "btrfs", "findmnt", "btrfs-scrub"],
     "smartmontools btrfs-progs util-linux", "smartmontools btrfs-progs util-linux"),
    ("Containers & VMs", ["podman", "virsh", "machinectl", "distrobox", "systemd-run", "qemu-system-x86_64"],
     "podman libvirt machinectl distrobox systemd qemu", "podman libvirt-daemon-system machinectl distrobox systemd qemu-kvm"),
    ("NFC", ["nfc-list", "nfc-scan-device"],
     "libnfc", "libnfc-bin"),
    ("Infrared", ["ir-ctl", "mode2"],
     "v4l-utils", "v4l-utils"),
    ("Printing", ["lp", "lpstat", "lpoptions", "lpinfo"],
     "cups", "cups-client"),
    ("OCR & documents", ["tesseract", "pdftotext", "pdffonts", "pdfinfo", "pdftoppm", "magick", "ffmpeg", "ffprobe"],
     "tesseract poppler imagemagick ffmpeg", "tesseract-ocr poppler-utils imagemagick ffmpeg"),
    ("Camera & video", ["v4l2-ctl", "gst-launch", "ffmpeg", "ffplay"],
     "v4l-utils gstreamer ffmpeg", "v4l-utils gstreamer1.0-tools ffmpeg"),
    ("Screenshot", ["grim", "slurp", "screenshot", "xdotool", "xwininfo"],
     "grim slurp gnome-screenshot xdotool x11-utils", "grim slurp gnome-screenshot xdotool x11-utils"),
    ("Shell UI", ["niri", "wtype", "wl-copy", "wl-paste", "notify-send", "gsettings", "gdbus", "dbus-send"],
     "niri wtype wl-clipboard libnotify glib2 dbus", "niri wtype wl-clipboard libnotify-bin glib2.0-bin dbus"),
    ("Chronoa shell bridge", ["python3", "gjs", "busctl"],
     "python gjs dbus", "python3 gjs dbus"),
    ("Firmware", ["fwupdmgr"],
     "fwupd", "fwupd"),
    ("Security", ["mokutil", "sbctl", "auditctl", "nft", "iptables", "ufw"],
     "mokutil sbctl audit nftables iptables ufw", "mokutil sbctl auditd nftables iptables ufw"),
    ("System", ["systemctl", "journalctl", "loginctl", "hostnamectl", "timedatectl", "localectl"],
     "systemd", "systemd"),
    ("Power", ["cpupower", "powerprofilesctl"],
     "cpupower power-profiles-daemon", "linux-cpupower power-profiles-daemon"),
    ("Graphics", ["glxinfo", "nvidia-smi"],
     "mesa-utils nvidia-utils", "mesa-utils nvidia-utils"),
    ("Network tools", ["ip", "ping", "mtr", "nmap", "iperf3", "curl", "wget", "host", "delv"],
     "iproute2 iputils mtr nmap iperf3 curl wget bind-tools", "iproute2 iputils mtr nmap iperf3 curl wget dnsutils"),
    ("VPN", ["tailscale", "wireguard-tools"],
     "tailscale wireguard-tools", "tailscale wireguard-tools"),
    ("Storage", ["btrfs", "findmnt", "compsize", "du", "df"],
     "btrfs-progs util-linux", "btrfs-progs util-linux"),
    ("Backup", ["restic", "rsync"],
     "restic rsync", "restic rsync"),
    ("Containers", ["podman", "distrobox", "machinectl", "systemd-run", "systemd-vmspawn"],
     "podman distrobox systemd", "podman distrobox systemd-container"),
    ("Virtualization", ["virsh", "qemu-system-x86_64", "swtpm"],
     "libvirt qemu-desktop swtpm", "libvirt-daemon-system qemu-kvm swtpm"),
    ("Bluetooth", ["bluetoothctl", "busctl", "gatttool", "mode2"],
     "bluez-utils dbus bluez", "bluez-utils dbus bluez"),
    ("NFC", ["nfc-list", "nfc-scan-device", "nfc-mfultralight"],
     "libnfc", "libnfc-bin"),
    ("Infrared", ["ir-ctl", "mode2"],
     "v4l-utils", "v4l-utils"),
    ("Printing", ["lp", "lpstat", "lpoptions", "lpinfo", "scanimage", "sane-find-scanner"],
     "cups sane", "cups-client sane-utils"),
    ("File tools", ["7z", "lrzcat", "lzop", "btrfs", "magick", "ffmpeg", "ffprobe", "pdftoppm", "pdfinfo", "pdffonts", "pdfunite", "qrencode"],
     "p7zip lrzip lzop btrfs-progs imagemagick ffmpeg poppler qrencode", "p7zip lrzip lzop btrfs-progs imagemagick ffmpeg poppler-utils qrencode"),
    ("Misc", ["git", "ip", "last", "who", "cancellation", "coredumpctl", "crontab", "systemd-analyze", "loginctl", "hostnamectl", "timedatectl", "localectl", "cpupower", "powerprofilesctl", "nvidia-smi", "glxinfo", "pidstat", "iostat", "mpstat", "sar", "ethtool", "i2cdetect", "i2cget", "i2cset", "gpspipe", "nethogs", "vnstat", "liquidctl", "yt-dlp", "wget", "curl", "nc", "iperf3", "mtr", "nmap", "delv", "host", "tldr", "flatpak", "gtk-launch", "xdg-user-dir", "xdg-mime", "xdg-settings", "shani-deploy", "shani-chronoa", "shani-chronoa-screen-stream"],
     "git iproute2 util-linux coreutils systemd systemd-coredump cronie systemd-analyze systemd power-profiles-daemon nvidia-utils mesa-utils sysstat ethtool i2c-tools gpsd nethogs vnstat liquidctl yt-dlp wget curl netcat-openbsd iperf3 mtr nmap bind-tools tldr flatpak gtk3 xdg-user-dirs xdg-utils shani-deploy shani-chronoa",
     "git iproute2 util-linux coreutils systemd systemd-coredump cron systemd-analyze systemd power-profiles-daemon nvidia-utils mesa-utils sysstat ethtool i2c-tools gpsd nethogs vnstat liquidctl yt-dlp wget curl netcat-openbsd iperf3 mtr nmap dnsutils tldr flatpak gtk3 xdg-user-dirs xdg-utils shani-deploy shani-chronoa"),
    ("Android", ["adb"],
     "android-tools", "android-tools-adb"),
    ("Calculator / math", ["bc"],
     "bc", "bc"),
    ("Kernel messages", ["dmesg"],
     "util-linux", "util-linux"),
    ("File users", ["fuser"],
     "psmisc", "psmisc"),
    ("GLib introspection", ["gio"],
     "glib2", "libglib2.0-bin"),
    ("Logs", ["logrotate"],
     "logrotate", "logrotate"),
    ("Open file descriptors", ["lsof"],
     "lsof", "lsof"),
    ("Screen reader", ["orca"],
     "orca", "orca"),
    ("Package manager", ["pacman"],
     "pacman", "apt"),
    ("ELF reader", ["readelf"],
     "binutils", "binutils"),
    ("GPU info", ["vulkaninfo"],
     "vulkan-tools", "vulkan-tools"),
    ("Power", ["upower"],
     "upower", "upower"),
    ("USB devices", ["usb-devices"],
     "usbutils", "usbutils"),
    ("X11 input", ["xinput", "xprop", "xsel"],
     "xorg-xinput xorg-xprop xsel", "xinput x11-utils xsel"),
    ("X11 window", ["xset", "xwd", "xwininfo"],
     "xorg-xset xorg-xwd x11-utils", "xset x11-utils x11-utils"),
    ("Wayland input", ["ydotool"],
     "ydotool", "ydotool"),
    ("QR code", ["zbarimg"],
     "zbar", "zbar-tools"),
    ("Bootloader", ["bootctl"],
     "systemd-boot", "systemd-boot"),
    ("Bubblewrap", ["bwrap"],
     "bubblewrap", "bubblewrap"),
    ("Cancel", ["cancel"],
     "systemd", "systemd"),
    ("DDC/CI", ["ddcutil"],
     "ddcutil", "ddcutil"),
    ("Images", ["import", "pdfimages"],
     "imagemagick poppler-utils", "imagemagick poppler-utils"),
    ("Location", ["lynx"],
     "lynx", "lynx"),
    ("NFC", ["nfc-emulate-forum-tag4", "nfc-jewel"],
     "libnfc", "libnfc-bin"),
    ("Patch", ["patch"],
     "patch", "patch"),
    ("Plocate", ["plocate"],
     "plocate", "plocate"),
    ("RFKill", ["rfkill"],
     "util-linux", "util-linux"),
    ("ROCm SMI", ["rocm-smi"],
     "rocm-smi", "rocm-smi"),
    ("RTL FM", ["rtl_fm"],
     "rtl-sdr", "rtl-sdr"),
    ("Rubberband", ["rubberband"],
     "rubberband", "rubberband"),
    ("Shutdown", ["shutdown"],
     "systemd", "systemd"),
    ("SoundStretch", ["soundstretch"],
     "soundstretch", "soundstretch"),
    ("Systemd inhibit", ["systemd-inhibit"],
     "systemd", "systemd"),
    ("System settings", ["systemsettings"],
     "systemsettings5", "systemsettings"),
    ("UDisks", ["udisksctl"],
     "udisks2", "udisks2"),
    ("X11 clipboard", ["xclip"],
     "xclip", "xsel"),
    ("X11 input", ["xinput"],
     "xorg-xinput", "xorg-xinput"),
    ("File indexer", ["balooctl6"],
     "baloo", "baloo-kf5"),
    ("PulseAudio player", ["paplay"],
     "pulseaudio", "pulseaudio-utils"),
    ("Cloud sync", ["rclone"],
     "rclone", "rclone"),
    ("Vulkan info", ["vulkaninfo"],
     "vulkan-tools", "vulkan-tools"),
]


def distro_id():
    try:
        with open("/etc/os-release") as f:
            info = {}
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    info[k.strip()] = v.strip().strip('"')
            return info.get("ID", "").lower()
    except OSError:
        return ""


def install_cmd(distro, arch_pkg, deb_pkg):
    arch_family = {"arch", "endeavouros", "manjaro"}
    deb_family = {"ubuntu", "debian", "linuxmint", "pop", "zorin", "raspbian"}
    if distro in arch_family:
        return "sudo pacman -S " + arch_pkg
    if distro in deb_family:
        return "sudo apt install " + deb_pkg
    return "install " + arch_pkg


def main():
    distro = distro_id()
    missing_any = False
    print("shani-chronoa-doctor: tools shani-chronoa needs")
    print("=" * 56)
    for name, tools, arch_pkg, deb_pkg in FEATURES:
        print("\n" + name)
        feature_missing = False
        for tool in tools:
            present = shutil.which(tool) is not None
            if not present:
                missing_any = True
                feature_missing = True
            print("  [{}] {}".format("OK " if present else "MISSING", tool))
        if feature_missing:
            print("    -> {}".format(install_cmd(distro, arch_pkg, deb_pkg)))
    print()
    if missing_any:
        print("Some tools are missing. Install them, then restart shani-chronoa.")
        return 1
    print("All checked tools are present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())