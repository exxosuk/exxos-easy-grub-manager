#!/bin/bash
# Build the .deb package for exxos-easy-grub-manager
set -e

VERSION="1.0.4"
PKG="exxos-easy-grub-manager"
ARCH="all"
BUILD_DIR="build/${PKG}_${VERSION}_${ARCH}"

rm -rf build dist
mkdir -p dist

# Create directory structure
mkdir -p "${BUILD_DIR}/DEBIAN"
mkdir -p "${BUILD_DIR}/usr/bin"
mkdir -p "${BUILD_DIR}/usr/share/applications"
mkdir -p "${BUILD_DIR}/usr/share/${PKG}"

# Copy main script
cp src/exxos-easy-grub-manager.py "${BUILD_DIR}/usr/share/${PKG}/"
chmod 755 "${BUILD_DIR}/usr/share/${PKG}/exxos-easy-grub-manager.py"

# Create launcher script
cat > "${BUILD_DIR}/usr/bin/exxos-easy-grub-manager" << 'EOF'
#!/bin/sh
exec python3 /usr/share/exxos-easy-grub-manager/exxos-easy-grub-manager.py "$@"
EOF
chmod 755 "${BUILD_DIR}/usr/bin/exxos-easy-grub-manager"

# Create .desktop file
cat > "${BUILD_DIR}/usr/share/applications/exxos-easy-grub-manager.desktop" << EOF
[Desktop Entry]
Name=Exxos Easy GRUB Manager
Comment=Manage GRUB bootloader installations across drives
Exec=exxos-easy-grub-manager
Icon=system-run
Terminal=false
Type=Application
Categories=System;Settings;
Keywords=grub;boot;bootloader;
EOF

# Create control file
cat > "${BUILD_DIR}/DEBIAN/control" << EOF
Package: ${PKG}
Version: ${VERSION}
Section: admin
Priority: optional
Architecture: ${ARCH}
Depends: python3, python3-pyqt5, grub-pc | grub-efi-amd64, os-prober
Maintainer: exxos_uk@yahoo.co.uk
Description: Easy GUI tool for managing GRUB bootloader
 Exxos Easy GRUB Manager provides a graphical interface for
 detecting bootable partitions, installing and removing GRUB
 on drives, saving and restoring GRUB settings, and a one-click
 Fix All button to repair boot configurations.
Homepage: https://github.com/exxosuk/exxos-easy-grub-manager
EOF

# Build
dpkg-deb --build "${BUILD_DIR}" "dist/${PKG}_${VERSION}_${ARCH}.deb"
echo ""
echo "Built: dist/${PKG}_${VERSION}_${ARCH}.deb"
