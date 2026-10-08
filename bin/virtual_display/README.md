# NKAS Android virtual-display bridge

`nkas-vd-server.jar` runs on Android through `app_process`. It creates a
720x1280 virtual display backed by `ImageReader` and sends raw RGB frames through
an Android local socket. Alpha is discarded like MAA-Meow, while the channel
order follows NKAS's RGB screenshot contract. This avoids device-specific
premultiplied-alpha and Bitmap color conversion. The host only needs ADB and Python, so the same JAR is
used from Windows, x86_64 Linux, and ARM Linux.

The optional fifth server argument is a persistent NKAS virtual-display ID. It
is used in the Android display name (`NIKKE-<id>`); Android still assigns its
own numeric display ID for each runtime instance.

Managed NKAS sessions always supply the stable ID stored at
`Emulator.PhysicalDevice.VirtualDisplayId`. Invalid supplied IDs are rejected.
`READY` and `INFO` report the stable identity, current numeric ID, server PID,
socket name, dimensions and rotation. A new socket identifies each recreation.

The backend owns the bridge through `module/device/adb/virtual_display_session.py`;
workers attach through local IPC. Stopping or crashing a worker retains a valid
screen. Normal backend shutdown stops workers before releasing verified bridge
processes and ADB forwards. Runtime records live in ignored
`config/.virtual_display/`; failed cleanup retains records for the next owner.

WebUI and mobile control resolve `/api/{name}/virtual-display` and connect to
that existing screen. An unavailable managed target never falls back to display
0. Mobile uses scrcpy `display_id`, without creating another virtual display.
After a screen changes, reconnect through the existing control button.

The bridge also loads `bin/scrcpy/scrcpy-server` for its Android compatibility
context. It does not start scrcpy video capture or depend on a host scrcpy
executable.

Source is in `src/com/nkas/virtualdisplay/Server.java`. The checked-in JAR is a
DEX JAR built with Java 8 bytecode, D8, and Android min API 28.
