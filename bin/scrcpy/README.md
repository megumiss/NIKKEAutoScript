# Interactive preview

`scrcpy-server` is the Android server from scrcpy **4.1** (Apache-2.0).
Source and license: https://github.com/Genymobile/scrcpy/tree/v4.1

SHA-256: `deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae`

The homepage control button starts this server through the configured ADB.
Python forwards H.264 packets and input over a same-origin WebSocket; the Vue
player uses WebCodecs when available and TinyH264 for browsers without WebCodecs
(including ordinary LAN HTTP). No scrcpy desktop client or ws-scrcpy service is
required. Audio is disabled. Closing control, changing instance, or leaving the
page releases the session. Each session has its own socket, temporary JAR and
Android process; it does not stop another scrcpy client or the virtual display.

The protocol is pinned to 4.1, including its session-size packets and frame flag
positions. Update `module/device/adb/scrcpy.py` and its hash check together when
replacing the binary. The virtual-display bridge also loads this
JAR; validate that path when changing versions.

The resolved ADB serial and virtual display ID come from the running task's
preview. Without a running task, `auto` works only with one online ADB device.
Virtual-display control requires a running task that has published its screen;
it never falls back to display 0. Existing bitrate/FPS settings still apply.

The control bar includes a clipboard panel for explicit device/local clipboard
reads and writes, and a bitrate panel (0.1–64 Mbps). Applying a bitrate saves
`Emulator.Scrcpy.Bitrate` and reconnects after the old session has released its
resources. The 4.1 protocol cannot change a running encoder's bitrate directly.
Clipboard access is user initiated; LAN HTTP users can paste text manually when
the browser does not expose its Clipboard API.

The screenshot button downloads the currently decoded frame as a PNG at its
original pixel dimensions, without the toolbar. It works with both WebCodecs
and TinyH264 and does not request another frame over ADB.

For a manual regression, open an ADB instance's homepage preview, enter control,
check click/drag and Back/Home, rotate the device and check coordinates again.
Test both localhost/HTTPS and LAN HTTP, then exit, re-enter, switch instances and
disconnect ADB. Repeat with a running virtual-display task and verify that the
main display is unaffected. For a slow connection, lower bitrate and FPS in the
interactive-control settings.
