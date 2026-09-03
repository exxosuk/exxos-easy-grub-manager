#!/usr/bin/env python3
"""
Exxos Easy GRUB Manager - GUI tool for managing GRUB bootloader across drives.
Left panel: detected bootable OS partitions.
Right panel: drives where GRUB can be installed/removed.
"""

import sys
import os
import subprocess
import json
import shutil
import tempfile
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGroupBox, QTreeWidget, QTreeWidgetItem, QPushButton, QLabel,
    QMessageBox, QTextEdit, QHeaderView, QSplitter, QFileDialog,
    QProgressBar
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor


class CmdWorker(QThread):
    """Run a sudo command in a background thread so the UI stays responsive."""
    finished = pyqtSignal(bool, str, str)  # success, stdout, stderr

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        try:
            result = subprocess.run(
                f"pkexec env PATH=/usr/sbin:/usr/bin:/sbin:/bin {self.cmd}", shell=True,
                capture_output=True, text=True, timeout=120
            )
            self.finished.emit(
                result.returncode == 0,
                result.stdout.strip(),
                result.stderr.strip()
            )
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "", "Command timed out")
        except Exception as e:
            self.finished.emit(False, "", str(e))

VERSION = "1.0.5"

BACKUP_DIR = os.path.expanduser("~/.config/exxos-grub-manager")
GRUB_FILES = [
    "/etc/default/grub",
    "/boot/grub/grub.cfg",
]
GRUB_CUSTOM_DIR = "/etc/grub.d"


def run_cmd(cmd, check=False):
    """Run a command and return stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if check and r.returncode != 0:
            return None
        return r.stdout.strip()
    except Exception:
        return None


def is_root():
    return os.geteuid() == 0


def get_block_devices():
    """Get all block devices with partition info."""
    out = run_cmd("lsblk -J -o NAME,TYPE,FSTYPE,SIZE,LABEL,MOUNTPOINT,UUID,PARTTYPE")
    if not out:
        return []
    try:
        data = json.loads(out)
        return data.get("blockdevices", [])
    except json.JSONDecodeError:
        return []


def _entry_in(base, name):
    """Case-insensitive lookup of one name inside base. Returns the full path or ''."""
    if not base:
        return ""
    try:
        for entry in os.listdir(base):
            if entry.lower() == name.lower():
                return os.path.join(base, entry)
    except OSError:
        return ""
    return ""


def _path_in(base, *names):
    """Walk a path under base one name at a time, ignoring case. Returns '' if missing."""
    current = base
    for name in names:
        current = _entry_in(current, name)
        if not current:
            return ""
    return current


def windows_install_at(mount):
    """True if a real Windows installation lives here.

    Every Windows install keeps its registry hive at \\Windows\\System32\\config\\SYSTEM.
    A backup or data partition never has one, however it is labelled.
    """
    return bool(_path_in(mount, "Windows", "System32", "config", "SYSTEM"))


def windows_boot_at(mount):
    """True if this is a Windows boot / System Reserved partition."""
    return bool(_path_in(mount, "bootmgr")) and bool(_path_in(mount, "Boot", "BCD"))


def linux_install_at(mount):
    """True if a Linux root filesystem lives here.

    Needs /etc plus a release file - a partition holding only /boot, or an ext4
    drive full of backups, is not something GRUB can boot.
    """
    if not mount or not os.path.isdir(os.path.join(mount, "etc")):
        return False
    for release in ("etc/os-release", "etc/lsb-release", "etc/mx-version",
                    "etc/debian_version", "etc/redhat-release"):
        if os.path.exists(os.path.join(mount, release)):
            return True
    return False


def probe_mount(device):
    """Mount a partition read-only somewhere temporary so it can be inspected.

    Only possible as root; a normal user cannot read an unmounted partition at all.
    Returns the temporary mountpoint, or None.
    """
    if not is_root():
        return None
    try:
        tmp = tempfile.mkdtemp(prefix="exxos-grub-probe-")
    except OSError:
        return None
    if run_cmd(f"mount -o ro,noatime {device} {tmp}", check=True) is None:
        try:
            os.rmdir(tmp)
        except OSError:
            pass
        return None
    return tmp


def probe_unmount(tmp):
    """Undo probe_mount."""
    run_cmd(f"umount {tmp}")
    try:
        os.rmdir(tmp)
    except OSError:
        pass


def identify_os(device, fstype, label, mount):
    """Work out which OS, if any, is installed on a partition.

    Returns (os_name, reason). os_name is empty when there is no operating
    system on the partition, and reason then says why, for the log.
    """
    if not mount:
        return "", "not mounted - mount it or run as root to check"

    if fstype in ("ext2", "ext3", "ext4", "btrfs", "xfs"):
        if linux_install_at(mount):
            return detect_linux_os(device, mount), ""
        return "", "no operating system installed (data partition)"

    if fstype == "ntfs":
        if windows_install_at(mount):
            return f"Windows ({label})" if label else "Windows", ""
        if windows_boot_at(mount):
            return "Windows Boot (System Reserved)", ""
        return "", "no operating system installed (data partition)"

    return "", "not a filesystem an OS can be installed on"


def detect_bootable_partitions():
    """Detect the partitions that actually contain a bootable OS.

    The filesystem type on its own proves nothing: an ext4 or NTFS partition is
    just as likely to be a backup or data drive as an installed system. So every
    candidate is opened and checked for the files a real installation has, and
    anything else is reported as skipped rather than listed as bootable.

    Returns (entries, skipped).
    """
    entries = []
    skipped = []
    devices = get_block_devices()

    for dev in devices:
        children = dev.get("children", [])
        if not children:
            children = [dev]

        for part in children:
            if part.get("type") not in ("part", "disk"):
                continue

            name = "/dev/" + part["name"]
            fstype = part.get("fstype") or ""
            label = part.get("label") or ""
            size = part.get("size") or ""
            uuid = part.get("uuid") or ""
            mount = part.get("mountpoint") or ""

            if not fstype or fstype in ("swap", "vfat", "iso9660", "squashfs"):
                # Swap, EFI/removable vfat and optical media are never a bootable
                # OS in their own right, so they are not worth opening.
                continue

            probe = None
            if not mount:
                probe = probe_mount(name)
                mount = probe or ""
            try:
                os_name, reason = identify_os(name, fstype, label, mount)
            finally:
                if probe:
                    probe_unmount(probe)

            if not os_name:
                skipped.append({
                    "device": name,
                    "fstype": fstype,
                    "label": label,
                    "reason": reason,
                })
                continue

            entries.append({
                "device": name,
                "fstype": fstype,
                "size": size,
                "label": label,
                "uuid": uuid,
                "mount": part.get("mountpoint") or "",
                "os": os_name,
            })

    # os-prober can find systems we could not open ourselves; it needs root.
    os_prober_results = run_cmd("os-prober 2>/dev/null")
    if os_prober_results:
        for line in os_prober_results.splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                dev = parts[0].split("@")[0]
                os_name = parts[1]
                found = False
                for e in entries:
                    if e["device"] == dev:
                        if not e["os"] or e["os"].startswith("Unknown"):
                            e["os"] = os_name
                        found = True
                        break
                if not found:
                    entries.append({
                        "device": dev,
                        "fstype": "",
                        "size": "",
                        "label": "",
                        "uuid": "",
                        "mount": "",
                        "os": os_name,
                    })
                    skipped = [s for s in skipped if s["device"] != dev]

    return entries, skipped


def detect_linux_os(device, mountpoint):
    """Try to identify the Linux OS on a partition."""
    if mountpoint:
        for f in ["/etc/mx-version", "/etc/os-release", "/etc/lsb-release"]:
            path = mountpoint + f
            if os.path.exists(path):
                content = run_cmd(f"head -1 {path}")
                if content:
                    if "mx-version" in f:
                        return content
                    for line in content.splitlines():
                        if line.startswith("PRETTY_NAME="):
                            return line.split("=", 1)[1].strip('"')
        return f"Linux ({mountpoint})"

    content = run_cmd(f"blkid -o value -s LABEL {device}")
    if content:
        return f"Linux ({content})"
    return "Linux"


def size_to_mb(size_str):
    """Convert an lsblk size string like '0B', '100M' or '55.8G' to MB."""
    try:
        s = size_str.strip().upper()
        if s.endswith("B"):
            s = s[:-1]
        units = {"K": 1.0 / 1024, "M": 1.0, "G": 1024.0,
                 "T": 1024.0 * 1024, "P": 1024.0 * 1024 * 1024}
        if s and s[-1] in units:
            return float(s[:-1]) * units[s[-1]]
        return float(s) / (1024 * 1024) if s else 0
    except (ValueError, IndexError):
        return 0


def root_disk():
    """The whole disk the running system boots from, e.g. /dev/sdb or /dev/nvme0n1."""
    root_dev = run_cmd("findmnt -n -o SOURCE /")
    if not root_dev:
        return ""
    parent = run_cmd(f"lsblk -no PKNAME {root_dev}")
    if parent:
        parent = parent.splitlines()[0].strip()
    if parent:
        return "/dev/" + parent
    return root_dev


def get_drives():
    """Get physical drives (not partitions) for GRUB installation targets."""
    drives = []
    devices = get_block_devices()
    for dev in devices:
        if dev.get("type") == "disk":
            name = "/dev/" + dev["name"]
            size = dev.get("size", "")
            # An empty card reader or drive bay reports 0B. There is no medium
            # in it, so it is not somewhere GRUB can be installed.
            if size_to_mb(size) <= 0 and not dev.get("children"):
                continue
            label = dev.get("label") or ""
            partitions = []
            for child in dev.get("children", []):
                parts = []
                clabel = child.get("label") or ""
                cmount = child.get("mountpoint") or ""
                cfs = child.get("fstype") or ""
                if clabel:
                    parts.append(clabel)
                if cmount:
                    parts.append(cmount)
                elif cfs == "ntfs":
                    parts.append("NTFS")
                if parts:
                    partitions.append(", ".join(parts))
            contents = " | ".join(partitions) if partitions else ""
            has_grub = check_grub_on_drive(name)
            drives.append({
                "device": name,
                "size": size,
                "label": label,
                "contents": contents,
                "has_grub": has_grub,
            })
    return drives


def check_grub_on_drive(device):
    """Check if GRUB is installed in the MBR of a drive."""
    # Try reading MBR directly (works if user has read access to device)
    out = run_cmd(f"dd if={device} bs=512 count=1 2>/dev/null | strings | grep -i GRUB")
    if out:
        return True
    # Check if dd actually failed due to permissions vs no GRUB
    test = run_cmd(f"dd if={device} bs=1 count=1 2>&1")
    if test is None or "Permission denied" in (test or "") or "Operation not permitted" in (test or ""):
        # Can't read device - check debconf for install target
        out = run_cmd("debconf-show grub-pc 2>/dev/null")
        if out and device in out:
            return True
        # Also unknown, assume installed if it's the boot disk
        if device == root_disk():
            return True
    return False


def list_saved_backups():
    """List available GRUB config backups, newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    backups = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "grub")):
            backups.append(name)
    return backups


class GrubManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Exxos Easy GRUB Manager {VERSION}")
        self.setMinimumSize(950, 550)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Warning if not root
        if not is_root():
            warn = QLabel("Not running as root - operations will prompt for password")
            warn.setStyleSheet("color: orange; font-weight: bold; padding: 5px;")
            layout.addWidget(warn)

        # Main splitter
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left panel - detected OS partitions
        left_group = QGroupBox("Detected Bootable Partitions")
        left_layout = QVBoxLayout(left_group)

        self.os_tree = QTreeWidget()
        self.os_tree.setHeaderLabels(["Include", "Device", "OS / Label", "Type", "Size"])
        self.os_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.os_tree.setRootIsDecorated(False)
        left_layout.addWidget(self.os_tree)

        splitter.addWidget(left_group)

        # Right panel - GRUB install targets
        right_group = QGroupBox("GRUB Install Targets (Drives)")
        right_layout = QVBoxLayout(right_group)

        self.drive_tree = QTreeWidget()
        self.drive_tree.setHeaderLabels(["Select", "Drive", "Size", "Contents", "GRUB Status"])
        self.drive_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.drive_tree.setRootIsDecorated(False)
        right_layout.addWidget(self.drive_tree)

        # Install/Remove buttons
        btn_layout = QHBoxLayout()
        self.install_btn = QPushButton("Install GRUB to Selected")
        self.install_btn.setStyleSheet("background-color: #2e7d32; color: white; padding: 8px;")
        self.install_btn.clicked.connect(self.install_grub)

        self.remove_btn = QPushButton("Remove GRUB from Selected")
        self.remove_btn.setStyleSheet("background-color: #c62828; color: white; padding: 8px;")
        self.remove_btn.clicked.connect(self.remove_grub)

        btn_layout.addWidget(self.install_btn)
        btn_layout.addWidget(self.remove_btn)
        right_layout.addLayout(btn_layout)

        splitter.addWidget(right_group)
        splitter.setSizes([500, 450])

        # Row 1: main action buttons
        row1 = QHBoxLayout()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.scan)

        self.update_grub_btn = QPushButton("Update GRUB Package")
        self.update_grub_btn.setToolTip("Updates GRUB to latest version, reinstalls to drives, and regenerates config")
        self.update_grub_btn.clicked.connect(self.update_grub)

        self.fix_all_btn = QPushButton("Fix All")
        self.fix_all_btn.setStyleSheet("background-color: #1565c0; color: white; padding: 8px; font-weight: bold;")
        self.fix_all_btn.setToolTip("Enable os-prober, detect all OSes, install GRUB to all drives, update config")
        self.fix_all_btn.clicked.connect(self.fix_all)

        row1.addWidget(self.refresh_btn)
        row1.addWidget(self.update_grub_btn)
        row1.addWidget(self.fix_all_btn)

        layout.addLayout(row1)

        # Row 2: save/restore and shortcut
        row2 = QHBoxLayout()

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setToolTip("Back up current GRUB config files")
        self.save_btn.clicked.connect(self.save_settings)

        self.restore_btn = QPushButton("Restore Settings")
        self.restore_btn.setToolTip("Restore GRUB config from a previous backup")
        self.restore_btn.clicked.connect(self.restore_settings)

        self.shortcut_btn = QPushButton("Create Desktop Shortcut")
        self.shortcut_btn.clicked.connect(self.toggle_shortcut)
        self.update_shortcut_btn_text()

        row2.addWidget(self.save_btn)
        row2.addWidget(self.restore_btn)
        row2.addStretch()
        row2.addWidget(self.shortcut_btn)

        layout.addLayout(row2)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(20)
        self.progress.setRange(0, 0)  # indeterminate by default
        self.progress.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("font-weight: bold;")
        self.progress_label.setVisible(False)

        prog_layout = QHBoxLayout()
        prog_layout.addWidget(self.progress_label)
        prog_layout.addWidget(self.progress)
        layout.addLayout(prog_layout)

        # Log
        self.log = QTextEdit()
        self.log.setMaximumHeight(120)
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Monospace", 9))
        layout.addWidget(self.log)

        # For blocking command execution
        self._cmd_result = None

        # Clicking a button left it holding focus, which the style draws as a
        # dotted rectangle that sits there afterwards saying nothing. Tab still
        # reaches the buttons, and then the rectangle means something.
        for button in self.findChildren(QPushButton):
            button.setFocusPolicy(Qt.TabFocus)

        self.scan()

    def log_msg(self, msg):
        self.log.append(msg)
        QApplication.processEvents()

    def set_controls_enabled(self, enabled):
        """Enable or disable all interactive controls."""
        for btn in (self.refresh_btn, self.update_grub_btn, self.fix_all_btn,
                    self.save_btn, self.restore_btn, self.shortcut_btn,
                    self.install_btn, self.remove_btn):
            btn.setEnabled(enabled)
        self.os_tree.setEnabled(enabled)
        self.drive_tree.setEnabled(enabled)

    def show_progress(self, message, step=None, total=None):
        """Show or update the progress bar with a message and lock controls."""
        self.set_controls_enabled(False)
        self.progress_label.setText(message)
        self.progress_label.setVisible(True)
        if step is not None and total is not None:
            self.progress.setRange(0, total)
            self.progress.setValue(step)
        else:
            self.progress.setRange(0, 0)  # indeterminate pulse
        self.progress.setVisible(True)
        QApplication.processEvents()

    def hide_progress(self):
        """Hide the progress bar and unlock controls."""
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)
        self.set_controls_enabled(True)
        QApplication.processEvents()

    def scan(self, clear_log=True):
        """Scan for bootable partitions and drives."""
        self.os_tree.clear()
        self.drive_tree.clear()
        if clear_log:
            self.log.clear()
        self.log_msg("Scanning for bootable partitions and drives...")

        QApplication.processEvents()

        partitions, skipped = detect_bootable_partitions()
        for p in partitions:
            item = QTreeWidgetItem()
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setText(1, p["device"])
            item.setText(2, p["os"])
            item.setText(3, p["fstype"])
            item.setText(4, p["size"])
            item.setData(0, Qt.UserRole, p)
            self.os_tree.addTopLevelItem(item)

        self.log_msg(f"Found {len(partitions)} bootable partition(s)")
        for s in skipped:
            label = f" \"{s['label']}\"" if s["label"] else ""
            self.log_msg(f"  not bootable: {s['device']} ({s['fstype']}){label} - {s['reason']}")

        drives = get_drives()
        for d in drives:
            item = QTreeWidgetItem()
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if d["has_grub"] else Qt.Unchecked)
            item.setText(1, d["device"])
            item.setText(2, d["size"])
            item.setText(3, d.get("contents", ""))
            status = "GRUB Installed" if d["has_grub"] else "No GRUB"
            item.setText(4, status)
            if d["has_grub"]:
                item.setForeground(4, QColor("#4caf50"))
            else:
                item.setForeground(4, QColor("#999"))
            item.setData(0, Qt.UserRole, d)
            self.drive_tree.addTopLevelItem(item)

        self.log_msg(f"Found {len(drives)} drive(s)")
        self.log_msg("Ready.")

    def get_selected_drives(self):
        """Get checked drives from the right panel."""
        selected = []
        for i in range(self.drive_tree.topLevelItemCount()):
            item = self.drive_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                selected.append(item.data(0, Qt.UserRole))
        return selected

    def get_all_drives(self):
        """Get all drives from the right panel."""
        all_drives = []
        for i in range(self.drive_tree.topLevelItemCount()):
            item = self.drive_tree.topLevelItem(i)
            all_drives.append(item.data(0, Qt.UserRole))
        return all_drives

    def run_sudo_cmd(self, cmd, description):
        """Run a command with pkexec, showing progress bar while it runs."""
        self.log_msg(f"Running: {description}")
        self.show_progress(description)

        self._cmd_result = None
        worker = CmdWorker(cmd)
        worker.finished.connect(self._on_cmd_finished)
        worker.start()

        # Process events while waiting so the progress bar animates
        while self._cmd_result is None:
            QApplication.processEvents()
            worker.msleep(50)

        success, stdout, stderr = self._cmd_result
        if success:
            if stdout:
                for line in stdout.splitlines():
                    self.log_msg(f"  {line}")
            self.log_msg("  OK")
        else:
            self.log_msg(f"  ERROR: {stderr}")
            if stdout:
                for line in stdout.splitlines():
                    self.log_msg(f"  {line}")
        return success

    def run_sudo_capture(self, cmd, description):
        """Run a command with pkexec and hand back its output rather than logging it."""
        self.show_progress(description)
        self._cmd_result = None
        worker = CmdWorker(cmd)
        worker.finished.connect(self._on_cmd_finished)
        worker.start()
        while self._cmd_result is None:
            QApplication.processEvents()
            worker.msleep(50)
        success, stdout, stderr = self._cmd_result
        if not success and stderr:
            self.log_msg(f"  {stderr}")
        return success, stdout

    def detect_grub_drives(self, drives):
        """Ask, as root, which drives really have GRUB in their MBR.

        The scan does this unprivileged and can only see the MBR of drives the
        user may read - usually none - so it has to be asked again here, once,
        before Fix All decides where GRUB should go. Returns None if the check
        could not be run, which is not the same as "no drive has GRUB".
        """
        devices = " ".join(d["device"] for d in drives)
        script = (f"for d in {devices}; do "
                  "dd if=$d bs=512 count=1 2>/dev/null | grep -qa GRUB && echo $d; "
                  "done")
        ok, out = self.run_sudo_capture(f"bash -c '{script}'",
                                        "Checking which drives have GRUB installed")
        if not ok:
            return None
        return [line.strip() for line in out.splitlines() if line.strip().startswith("/dev/")]

    def _on_cmd_finished(self, success, stdout, stderr):
        self._cmd_result = (success, stdout, stderr)

    # ── GRUB Install / Remove ──

    def install_grub(self):
        """Install GRUB to selected drives."""
        drives = self.get_selected_drives()
        if not drives:
            QMessageBox.warning(self, "No Selection", "Select at least one drive to install GRUB to.")
            return

        drive_list = "\n".join(f"  {d['device']} ({d['size']})" for d in drives)
        reply = QMessageBox.question(
            self, "Confirm Install",
            f"Install GRUB to:\n{drive_list}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            total = len(drives) + 1
            for i, d in enumerate(drives):
                dev = d["device"]
                self.show_progress(f"Installing GRUB to {dev}...", i, total)
                ok = self.run_sudo_cmd(f"grub-install {dev}", f"Installing GRUB to {dev}")
                if not ok:
                    QMessageBox.critical(self, "Error", f"Failed to install GRUB to {dev}.\nCheck the log below.")
                    return

            self.show_progress("Updating GRUB configuration...", len(drives), total)
            self.run_sudo_cmd("update-grub", "Updating GRUB configuration")
            self.log_msg("Done. Refreshing...")
        finally:
            self.hide_progress()
            self.scan(clear_log=False)

    def remove_grub(self):
        """Remove GRUB from selected drives by zeroing the MBR boot code."""
        drives = self.get_selected_drives()
        if not drives:
            QMessageBox.warning(self, "No Selection", "Select at least one drive to remove GRUB from.")
            return

        boot_disk = root_disk()
        if boot_disk:
            for d in drives:
                if d["device"] == boot_disk:
                    QMessageBox.critical(
                        self, "Safety Check",
                        f"Cannot remove GRUB from {boot_disk} - it is the current boot drive!\n"
                        "Removing it would make the system unbootable."
                    )
                    return

        drive_list = "\n".join(f"  {d['device']} ({d['size']})" for d in drives)
        reply = QMessageBox.critical(
            self, "Confirm REMOVAL",
            f"WARNING: This will remove GRUB from:\n{drive_list}\n\n"
            "The MBR boot code will be zeroed. This could make\n"
            "systems on these drives unbootable.\n\n"
            "Are you sure?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            total = len(drives)
            for i, d in enumerate(drives):
                dev = d["device"]
                self.show_progress(f"Removing GRUB from {dev}...", i, total)
                ok = self.run_sudo_cmd(
                    f"dd if=/dev/zero of={dev} bs=440 count=1",
                    f"Removing GRUB from {dev}"
                )
                if not ok:
                    QMessageBox.critical(self, "Error", f"Failed to remove GRUB from {dev}.")
                    break
        finally:
            self.hide_progress()
            self.log_msg("Done. Refreshing...")
            self.scan(clear_log=False)

    def update_grub(self):
        """Update GRUB packages, reinstall to drives, and regenerate config."""
        reply = QMessageBox.question(
            self, "Update GRUB",
            "This will:\n"
            "  1. Update the GRUB package to the latest version\n"
            "  2. Reinstall GRUB to all drives that currently have it\n"
            "  3. Regenerate grub.cfg with all detected OSes\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        grub_drives = [d for d in self.get_all_drives() if d["has_grub"]]
        total = 2 + len(grub_drives)
        step = 0

        try:
            step += 1
            self.show_progress("Updating GRUB packages...", step, total)
            self.run_sudo_cmd(
                "apt-get install --yes --only-upgrade grub-pc grub-common grub2-common",
                "Upgrading GRUB packages"
            )

            for d in grub_drives:
                step += 1
                dev = d["device"]
                self.show_progress(f"Reinstalling GRUB to {dev}...", step, total)
                ok = self.run_sudo_cmd(f"grub-install {dev}", f"Reinstalling GRUB to {dev}")
                if not ok:
                    self.log_msg(f"  WARNING: Failed on {dev}, continuing...")

            step += 1
            self.show_progress("Regenerating GRUB configuration...", step, total)
            ok = self.run_sudo_cmd("update-grub", "Regenerating GRUB configuration")

            if ok:
                self.log_msg("GRUB fully updated.")
                QMessageBox.information(self, "Success", "GRUB updated, reinstalled, and config regenerated.")
            else:
                QMessageBox.critical(self, "Error", "GRUB update had errors.\nCheck the log below.")
        finally:
            self.hide_progress()
            self.scan(clear_log=False)

    # ── Save / Restore Settings ──

    def save_settings(self):
        """Back up current GRUB config files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, timestamp)
        os.makedirs(backup_path, exist_ok=True)

        saved = []
        for src in GRUB_FILES:
            if os.path.exists(src):
                dst = os.path.join(backup_path, os.path.basename(src))
                shutil.copy2(src, dst)
                saved.append(src)

        # Back up custom grub.d scripts
        grubd_backup = os.path.join(backup_path, "grub.d")
        if os.path.isdir(GRUB_CUSTOM_DIR):
            shutil.copytree(GRUB_CUSTOM_DIR, grubd_backup)
            saved.append(GRUB_CUSTOM_DIR)

        # Write a manifest describing what was saved
        manifest = {
            "timestamp": timestamp,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "files": saved,
        }
        with open(os.path.join(backup_path, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)

        self.log_msg(f"Settings saved to: {backup_path}")
        self.log_msg(f"  Backed up: {', '.join(os.path.basename(s) for s in saved)}")
        QMessageBox.information(
            self, "Settings Saved",
            f"GRUB settings backed up to:\n{backup_path}\n\n"
            f"Files saved: {len(saved)}"
        )

    def restore_settings(self):
        """Restore GRUB config from a previous backup."""
        backups = list_saved_backups()
        if not backups:
            QMessageBox.information(
                self, "No Backups",
                f"No saved backups found in:\n{BACKUP_DIR}\n\n"
                "Use 'Save Settings' first to create a backup."
            )
            return

        # Build a selection dialog
        backup_list = []
        for name in backups:
            manifest_path = os.path.join(BACKUP_DIR, name, "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path) as f:
                    m = json.load(f)
                backup_list.append(f"{m.get('date', name)}  [{name}]")
            else:
                backup_list.append(name)

        from PyQt5.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Restore Settings",
            "Select a backup to restore:",
            backup_list, 0, False
        )
        if not ok:
            return

        # Extract the folder name from selection
        idx = backup_list.index(choice)
        backup_name = backups[idx]
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        reply = QMessageBox.warning(
            self, "Confirm Restore",
            f"This will overwrite your current GRUB configuration\n"
            f"with the backup from: {backup_name}\n\n"
            f"Current settings will be lost unless you save them first.\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Restore files using pkexec
        errors = False
        for src in GRUB_FILES:
            backup_file = os.path.join(backup_path, os.path.basename(src))
            if os.path.exists(backup_file):
                ok = self.run_sudo_cmd(
                    f"cp {backup_file} {src}",
                    f"Restoring {os.path.basename(src)}"
                )
                if not ok:
                    errors = True

        # Restore grub.d
        grubd_backup = os.path.join(backup_path, "grub.d")
        if os.path.isdir(grubd_backup):
            # Copy individual files from backup, don't delete existing
            for fname in os.listdir(grubd_backup):
                src = os.path.join(grubd_backup, fname)
                dst = os.path.join(GRUB_CUSTOM_DIR, fname)
                if os.path.isfile(src):
                    ok = self.run_sudo_cmd(f"cp {src} {dst}", f"Restoring grub.d/{fname}")
                    if not ok:
                        errors = True

        if errors:
            QMessageBox.warning(self, "Partial Restore", "Some files could not be restored. Check the log.")
        else:
            self.log_msg("Settings restored successfully.")
            QMessageBox.information(self, "Restored", "GRUB settings restored from backup.")

    # ── Fix All ──

    def fix_all(self):
        """One-click fix: enable os-prober, then repair GRUB where it already lives.

        Writing GRUB to every drive in the machine is not a repair, it is a
        change to drives that were working: a backup disk moved to another PC
        would suddenly try to boot this system. So Fix All puts GRUB back where
        it already is, and only falls back to the drive we are booted from when
        no drive has it at all.
        """
        drives = self.get_all_drives()

        if not drives:
            QMessageBox.warning(self, "No Drives", "No drives detected. Try Refresh first.")
            return

        detected = self.detect_grub_drives(drives)
        if detected is not None:
            for d in drives:
                d["has_grub"] = d["device"] in detected
            for i in range(self.drive_tree.topLevelItemCount()):
                item = self.drive_tree.topLevelItem(i)
                data = item.data(0, Qt.UserRole) or {}
                has = data.get("device") in detected
                data["has_grub"] = has
                item.setData(0, Qt.UserRole, data)
                item.setText(4, "GRUB Installed" if has else "No GRUB")
                item.setForeground(4, QColor("#4caf50") if has else QColor("#999"))

        targets = [d for d in drives if d["has_grub"]]
        booted = root_disk()
        fallback = not targets

        if fallback:
            targets = [d for d in drives if d["device"] == booted]
            if not targets:
                QMessageBox.warning(
                    self, "Fix All - No Target",
                    "No drive has GRUB installed, and the drive this system booted from\n"
                    f"({booted or 'unknown'}) is not in the list of drives.\n\n"
                    "Pick a drive yourself and use \"Install GRUB to Selected\"."
                )
                return

        target_text = "\n".join(
            f"  {d['device']}  {d['size']}  {d.get('contents', '')}" for d in targets
        )

        if fallback:
            msg = (
                "No drive on this machine has GRUB installed.\n\n"
                f"{booted} has been selected automatically, only because it is the\n"
                "drive this system is currently booted from. Nothing else was found\n"
                "to go on, so if that is the wrong drive, cancel and use\n"
                "\"Install GRUB to Selected\" instead.\n\n"
                "Fix All will:\n"
                "  1. Save current settings as backup\n"
                "  2. Enable os-prober (to detect Windows/other OSes)\n"
                f"  3. Install GRUB to {booted}\n"
                "  4. Run update-grub to detect all bootable OSes\n\n"
                "Continue?"
            )
        else:
            msg = (
                f"GRUB is already installed on:\n{target_text}\n\n"
                "Fix All will:\n"
                "  1. Save current settings as backup\n"
                "  2. Enable os-prober (to detect Windows/other OSes)\n"
                "  3. Reinstall GRUB there, leaving every other drive untouched\n"
                "  4. Run update-grub to detect all bootable OSes\n\n"
                "Continue?"
            )

        reply = QMessageBox.question(
            self, "Fix All - Confirm",
            msg,
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        total_steps = 3 + len(targets)
        step = 0

        try:
            self.log_msg("=== Fix All started ===")

            # Step 1: Save backup
            step += 1
            self.show_progress("Saving current settings...", step, total_steps)
            self.log_msg(f"[{step}/{total_steps}] Saving current settings as backup...")
            self.save_settings()

            # Step 2: Enable os-prober
            step += 1
            self.show_progress("Enabling os-prober...", step, total_steps)
            self.log_msg(f"[{step}/{total_steps}] Enabling os-prober...")
            script = (
                "grep -q '^GRUB_DISABLE_OS_PROBER=false' /etc/default/grub || "
                "( sed -i 's/^#*GRUB_DISABLE_OS_PROBER=.*/GRUB_DISABLE_OS_PROBER=false/' /etc/default/grub; "
                "grep -q '^GRUB_DISABLE_OS_PROBER=false' /etc/default/grub || "
                "echo 'GRUB_DISABLE_OS_PROBER=false' >> /etc/default/grub )"
            )
            self.run_sudo_cmd(f"bash -c \"{script}\"", "Enabling os-prober in GRUB config")

            # Step 3: install to the chosen drives - tick exactly those in the UI
            wanted = {d["device"] for d in targets}
            for i in range(self.drive_tree.topLevelItemCount()):
                item = self.drive_tree.topLevelItem(i)
                data = item.data(0, Qt.UserRole) or {}
                item.setCheckState(0, Qt.Checked if data.get("device") in wanted else Qt.Unchecked)

            if fallback:
                self.log_msg(f"  no drive had GRUB - defaulting to the booted drive {booted}")

            for d in targets:
                step += 1
                dev = d["device"]
                self.show_progress(f"Installing GRUB to {dev}...", step, total_steps)
                self.log_msg(f"[{step}/{total_steps}] Installing GRUB to {dev}...")
                ok = self.run_sudo_cmd(f"grub-install {dev}", f"Installing GRUB to {dev}")
                if not ok:
                    self.log_msg(f"  WARNING: Failed on {dev}, continuing...")

            # Step 4: Update GRUB config
            self.show_progress("Updating GRUB configuration...", total_steps, total_steps)
            self.log_msg(f"[{total_steps}/{total_steps}] Updating GRUB configuration...")
            ok = self.run_sudo_cmd("update-grub", "Running update-grub")

            self.log_msg("=== Fix All complete ===")

            if ok:
                installed = ", ".join(d["device"] for d in targets)
                note = (f"\n\n{booted} was chosen automatically because no drive had GRUB "
                        "on it; it is the drive this system booted from.") if fallback else ""
                QMessageBox.information(
                    self, "Fix All Complete",
                    f"GRUB has been installed and configured on {installed}.\n"
                    "All detected operating systems should now appear in the boot menu.\n\n"
                    "A backup of your previous settings was saved automatically." + note
                )
            else:
                QMessageBox.warning(
                    self, "Fix All - Warnings",
                    "Fix All completed with some warnings.\n"
                    "Check the log for details."
                )
        finally:
            self.hide_progress()
            self.scan(clear_log=False)

    # ── Desktop Shortcut ──

    def desktop_shortcut_path(self):
        return os.path.expanduser("~/Desktop/exxos-easy-grub-manager.desktop")

    def shortcut_exists(self):
        return os.path.exists(self.desktop_shortcut_path())

    def update_shortcut_btn_text(self):
        if self.shortcut_exists():
            self.shortcut_btn.setText("Remove Desktop Shortcut")
        else:
            self.shortcut_btn.setText("Create Desktop Shortcut")

    def toggle_shortcut(self):
        if self.shortcut_exists():
            os.remove(self.desktop_shortcut_path())
            self.log_msg("Desktop shortcut removed.")
        else:
            script_path = os.path.abspath(__file__)
            content = (
                "[Desktop Entry]\n"
                "Name=Exxos Easy GRUB Manager\n"
                "Comment=Manage GRUB bootloader installations\n"
                "Exec=python3 " + script_path + "\n"
                "Icon=system-run\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=System;\n"
            )
            with open(self.desktop_shortcut_path(), "w") as f:
                f.write(content)
            os.chmod(self.desktop_shortcut_path(), 0o755)
            self.log_msg(f"Desktop shortcut created: {self.desktop_shortcut_path()}")
        self.update_shortcut_btn_text()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Exxos Easy GRUB Manager")

    window = GrubManager()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
