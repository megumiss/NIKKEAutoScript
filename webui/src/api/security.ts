import { ref } from 'vue'

export const entryRequired = ref(false)
let recovering = false
const entryChanges = new Set<Promise<void>>()

export function beginEntryChange() {
  let finish!: () => void
  const pending = new Promise<void>(resolve => { finish = resolve })
  entryChanges.add(pending)
  return () => { entryChanges.delete(pending); finish() }
}

export function requireEntry() {
  entryRequired.value = true
  const invoke = (window as any).__TAURI__?.core?.invoke
  if (invoke && !recovering) {
    recovering = true
    // The native command validates the configured origin and never returns the key to JS.
    invoke('refresh_security_entry').catch(() => { recovering = false })
  }
}

export async function checkEntry(): Promise<boolean> {
  await Promise.all([...entryChanges])
  const response = await fetch('/api/system/status', { cache: 'no-store' })
  if (!response.ok) return false
  const status = await response.json()
  if (status.security_entry?.authorized === false) { requireEntry(); return false }
  if (entryRequired.value) location.reload()
  return true
}
