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
After a screen changes, reconnect through the existing control button. Preview
sampling uses the verified backend session and continues while viewers are
watching, including after the worker stops. Recreated sessions discard cached
frames from the previous bridge.

The bridge also loads `bin/scrcpy/scrcpy-server` for its Android compatibility
context. It does not start scrcpy video capture or depend on a host scrcpy
executable.

Each rendered frame is only bulk-copied as RGBA; alpha is dropped when a client
requests a frame, because the game renders far more often than NKAS reads.

## Protocol

One command per connection, terminated by `\n`. Connections are served on a
small thread pool, so a preview read does not delay a task screenshot.

| Command | Reply |
| --- | --- |
| `FRAME` | 12-byte big-endian `width, height, size`, then `size` RGB bytes (`size = width * height * 3`; all zero before the first frame). |
| `THUMB <factor>` | Same header and layout as `FRAME`, box-filtered down by `factor` (1–4). Used by the Web UI preview. |
| `INFO` | `OK id=<display> identity=<stable-id> pid=<pid> socket=<name> frames=<n> size=<w>x<h> rotation=<r> caps=thumb` |
| `START <package>` / `REPIN <package>` | `OK` or `ERROR ...` |
| `STOP` | `OK`, then the bridge releases the display and exits. |

Clients should check `caps=` in `INFO` before using `THUMB`; older JARs reply
`ERROR` and close the connection.

## Build

Source is in `src/com/nkas/virtualdisplay/Server.java`. The checked-in JAR is a
DEX JAR built with Java 8 bytecode, D8, and Android min API 28. Android classes
are only used through reflection, so no Android stubs are needed at compile
time. From this directory, with JDK 11+ and Android build-tools:

```sh
javac --release 8 -Xlint:all -d build/classes src/com/nkas/virtualdisplay/Server.java
java -cp <build-tools>/lib/d8.jar com.android.tools.r8.D8 --release --min-api 28 \
    --lib <sdk>/platforms/android-31/android.jar --output nkas-vd-server.jar build/classes/com/nkas/virtualdisplay/*.class
```
