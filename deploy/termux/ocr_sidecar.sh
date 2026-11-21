#!/system/bin/sh
# Start the NKAS OCR sidecar from adbd on an Android device running NKAS in Termux + proot-distro.
#
# Processes started by Termux inherit its cgroup, which some ROMs restrict to a
# few little cores while the app is in the background. Started through
# `adb shell run-as com.termux`, this script and the sidecar hang off adbd and
# can use every core. NKAS runs it automatically when the OcrSidecar deploy
# setting is on (see module/ocr/sidecar_launch.py):
#
#   adb -s <serial> shell run-as com.termux sh <repo>/deploy/termux/ocr_sidecar.sh <proot-distro>
set -eu
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export PATH="$PREFIX/bin:/system/bin"
export TMPDIR="$PREFIX/tmp"
repo=$(cd "$(dirname "$0")/../.." && pwd)
exec proot-distro run -b "$repo:/app/NIKKEAutoScript" -w /app/NIKKEAutoScript "${1:-nkas}" \
    -- /usr/local/bin/python -m module.ocr.sidecar
