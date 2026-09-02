#!/bin/bash
# Build the .deb and create a signed apt repo under docs/ for GitHub Pages
set -e

if [ -z "$EXXOS_GPG_KEY" ]; then
    echo "Usage: EXXOS_GPG_KEY=<key-id> ./build-apt-repo.sh"
    echo "Find your key with: gpg --list-keys exxos_uk@yahoo.co.uk"
    exit 1
fi

# Build the .deb first
bash build-deb.sh

DEB=$(ls dist/*.deb | head -1)
echo "Using: ${DEB}"

# Rebuild docs/ from scratch
rm -rf docs/pool docs/dists
mkdir -p docs/pool/main
mkdir -p docs/dists/stable/main/binary-amd64
mkdir -p docs/dists/stable/main/binary-all

cp "${DEB}" docs/pool/main/

# Generate Packages file
cd docs
dpkg-scanpackages pool/main /dev/null > dists/stable/main/binary-amd64/Packages
dpkg-scanpackages pool/main /dev/null > dists/stable/main/binary-all/Packages
gzip -k dists/stable/main/binary-amd64/Packages
gzip -k dists/stable/main/binary-all/Packages

# Generate Release file
cat > dists/stable/Release << EOF
Origin: exxos-easy-grub-manager
Label: Exxos Easy GRUB Manager
Suite: stable
Codename: stable
Architectures: amd64 all
Components: main
Description: Exxos Easy GRUB Manager apt repository
EOF

apt-ftparchive release dists/stable >> dists/stable/Release

# Sign
gpg --default-key "${EXXOS_GPG_KEY}" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
gpg --default-key "${EXXOS_GPG_KEY}" --armor --clearsign -o dists/stable/InRelease dists/stable/Release

# Export public key for users
gpg --armor --export "${EXXOS_GPG_KEY}" > exxos-easy-grub-manager.gpg.asc

cd ..
echo ""
echo "Apt repo built in docs/"
echo "Public key: docs/exxos-easy-grub-manager.gpg.asc"
