import { ref } from 'vue'

// Custom titlebar for the Tauri desktop shell, which runs without native
// window decorations.  The same SPA also loads in plain browsers, where
// __TAURI_INTERNALS__ is absent and the bar stays hidden.
const isTauri = Boolean((window as any).__TAURI_INTERNALS__)
const isMaximized = ref(false)

function tauriWindow(): any { return (window as any).__TAURI__?.window?.getCurrentWindow?.() }
async function tbMinimize() { await tauriWindow()?.minimize() }
async function tbToggleMaximize() { const win = tauriWindow(); if (!win) return; await win.toggleMaximize(); isMaximized.value = await win.isMaximized() }
async function tbHide() { await tauriWindow()?.hide() }
async function tbClose() { await tauriWindow()?.close() }
async function syncMaximized() { const win = tauriWindow(); if (win) isMaximized.value = await win.isMaximized() }

// The topbar previously also carried data-tauri-drag-region; on the header's
// own empty areas that native region raced with this handler and started the
// OS drag loop twice, which made dragging intermittently fail. Dragging is
// now driven solely from this mousedown handler (the window buttons are
// excluded). preventDefault also stops text selection from stealing the drag.
let lastDragDownAt = 0
let lastDragDownX = 0
let lastDragDownY = 0
// Shared window-drag driver for the in-page titlebar surfaces (topbar and
// sidebar brand). exclude is a selector for interactive children that must
// keep their own clicks (window buttons, sidebar toggle). Double-click
// toggles maximize, mirroring native windows.
function onWindowDragAreaMouseDown(event: MouseEvent, exclude: string) {
  if (!isTauri || event.button !== 0 || (event.target as HTMLElement).closest(exclude)) return
  const now = Date.now()
  if (now - lastDragDownAt < 300
    && Math.abs(event.clientX - lastDragDownX) < 5
    && Math.abs(event.clientY - lastDragDownY) < 5) {
    lastDragDownAt = 0
    tbToggleMaximize()
    return
  }
  lastDragDownAt = now
  lastDragDownX = event.clientX
  lastDragDownY = event.clientY
  event.preventDefault()
  tauriWindow()?.startDragging()
}

export function useTauriShell() {
  return { isTauri, isMaximized, tbMinimize, tbToggleMaximize, tbHide, tbClose, syncMaximized, onWindowDragAreaMouseDown }
}
