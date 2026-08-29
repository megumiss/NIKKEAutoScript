# NKAS Android virtual-display bridge

`nkas-vd-server.jar` runs on Android through `app_process`. It creates a
720x1280 virtual display backed by `ImageReader` and sends raw RGB frames through
an Android local socket. Alpha is discarded like MAA-Meow, while the channel
order follows NKAS's RGB screenshot contract. This avoids device-specific
premultiplied-alpha and Bitmap color conversion. The host only needs ADB and Python, so the same JAR is
used from Windows, x86_64 Linux, and ARM Linux.

The bridge also loads `bin/scrcpy/scrcpy-server` for its Android compatibility
context. It does not start scrcpy video capture or depend on a host scrcpy
executable.

Source is in `src/com/nkas/virtualdisplay/Server.java`. The checked-in JAR is a
DEX JAR built with Java 8 bytecode, D8, and Android min API 28.
