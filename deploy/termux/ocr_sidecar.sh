#!/system/bin/sh
# Start the NKAS OCR sidecar from adbd on an Android device running NKAS in Termux + proot-distro.
#
# Processes started by Termux inherit its cgroup, which some ROMs restrict to a
# few little cores while the app is in the background. Started through
# `adb shell run-as com.termux`, this script and the sidecar hang off adbd and
# can use every core. Set in config/deploy.yaml, for example:
#
#   OcrSidecarCommand: adb -s emulator-5554 shell run-as com.termux sh /data/data/com.termux/files/home/NIKKEAutoScript/deploy/termux/ocr_sidecar.sh
#
# The proot-distro name defaults to `nkas`; override with NKAS_PROOT_DISTRO.
set -eu
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export PATH="$PREFIX/bin:/system/bin"
export TMPDIR="$PREFIX/tmp"
repo=$(cd "$(dirname "$0")/../.." && pwd)
exec proot-distro run -b "$repo:/app/NIKKEAutoScript" -w /app/NIKKEAutoScript "${NKAS_PROOT_DISTRO:-nkas}" \
    -- /usr/local/bin/python -m module.ocr.sidecar
