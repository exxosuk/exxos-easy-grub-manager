# Changelog

Every released version of Exxos Easy GRUB Manager, newest first. The version
number in `build-deb.sh` is the one apt installs.

## 1.0.5

* The title bar shows the version, so which build is running is visible without
  asking apt.
* The version is now written once, in the source, and the package build reads it
  from there — the title bar and the installed package cannot disagree.

## 1.0.4

* **Fix All no longer installs GRUB to every drive.** It reinstalls only on the
  drives that already carry GRUB and leaves the rest alone. Writing a
  bootloader onto working data and backup disks is not a repair, and one of
  those disks moved to another machine would then try to boot this system.
* If no drive has GRUB at all, Fix All falls back to the drive the system
  booted from, and says so plainly in the dialog, in the log and in the
  completion message — including that the drive was chosen for want of
  anything better, and how to choose one yourself instead.
* Fix All now finds out which drives have GRUB by reading each MBR once with
  root rights before it decides. The ordinary scan cannot read an MBR as a
  normal user, so it could only ever guess.
* Buttons no longer keep keyboard focus after a click, so the dotted focus
  rectangle stops trailing the last button pressed. Tab still reaches them.

## 1.0.3

* The reason a partition is not bootable no longer names an operating system.
  It used to say "no Windows system installed" for NTFS and "no Linux system
  installed" for ext4, which tells the reader nothing — the line already shows
  the filesystem.

## 1.0.2

* **Only partitions that really contain an operating system are listed.**
  Detection went by filesystem type alone, so every ext4 and NTFS partition was
  reported as bootable and data and backup drives filled the list. Each
  candidate is now opened and checked for what an installation actually leaves
  behind: a Windows registry hive, or `/etc` with a release file beside it.
  Anything skipped is reported in the log with the reason.
* Partitions that are not mounted are probed with a temporary read-only mount
  when the program runs as root, and reported as unchecked otherwise, rather
  than guessed at. A normal user cannot read an unmounted partition at all.
* Empty card readers and drive bays — anything reporting 0 B because there is
  no medium in it — are no longer offered as places to install GRUB.
* The drive the system booted from is found with `lsblk -no PKNAME` instead of
  by stripping digits off the root device name. The old method turned an NVMe
  root partition into a device name that exists nowhere, so the safety check
  meant to stop GRUB being removed from the running boot drive could never
  match on an NVMe system.
* The repository is signed with the same key as the other Exxos repositories.
  Upgrading from 1.0.1 needs the key re-imported once (see the README) or apt
  reports `NO_PUBKEY`.

## 1.0.1

First release.

* Detects bootable partitions and lists the drives GRUB can be installed on.
* Installs and removes GRUB per drive, with a check that refuses to remove it
  from the drive the system is booted from.
* Updates the GRUB package, reinstalls it and regenerates the boot menu.
* Saves and restores GRUB configuration (`grub.cfg`, `/etc/default/grub`,
  `/etc/grub.d/`).
* Fix All: back up, enable os-prober, install GRUB, regenerate the config.
* Creates and removes a desktop launcher.
* Progress bar during every operation, with the controls locked while one runs.
* Uses `pkexec` for root access, so nothing needs a terminal.
