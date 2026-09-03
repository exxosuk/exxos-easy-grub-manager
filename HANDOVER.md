# Exxos Easy GRUB Manager — handover

**Version 1.0.5** · repo `exxosuk/exxos-easy-grub-manager`

**There is no local working copy.** The live script is edited in place at
`/usr/share/exxos-easy-grub-manager/exxos-easy-grub-manager.py`, and pushes are
made from a throwaway clone. Clone the repo somewhere sane before the next round
of work.

## What was fixed

- **Bootable detection.** It used to list every drive as bootable. It now opens
  each partition and looks for `\Windows\System32\config\SYSTEM`, or `/etc` plus a
  release file. Helpers: `_entry_in` / `_path_in` (case-insensitive), `probe_mount`,
  `identify_os`.
- **`root_disk()` uses `lsblk` PKNAME.** The previous approach stripped trailing
  digits from the device name, which is wrong for NVMe (`nvme0n1p1` → `nvme0n1`,
  not `nvme0n`). This guards the booted disk, so it matters.
- **Fix All** now installs GRUB only on drives that already have it, found with a
  single elevated MBR scan (`run_sudo_capture`, `detect_grub_drives`). If no drive
  has GRUB it falls back to the booted drive and warns.
- The "not bootable" message no longer names an operating system — a drive can be
  non-bootable Linux just as easily as non-bootable Windows. It reports the
  filesystem instead (NTFS, ext4, …).
- `VERSION` lives in the `.py` and is read by `build-deb.sh`; the title bar shows it.
- Buttons set to `Qt.TabFocus`.

## Packaging

`build-deb.sh` reads the version from the script. Sign the apt repo with the
Dolphin key `9B16A83279C5A435` — the older key for this project is gone.
