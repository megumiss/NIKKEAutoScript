export async function exportTextFile(url: string, filename: string): Promise<string | undefined> {
  const invoke = (window as any).__TAURI__?.core?.invoke
  if (typeof invoke === 'function') {
    return await invoke('save_export_file', { urlPath: url, filename })
  }

  const response = await fetch(url)
  if (!response.ok) {
    let message = `Export failed (${response.status})`
    try {
      const body = await response.json()
      message = body.error || body.message || message
    } catch {
      // Keep the HTTP status when the endpoint did not return JSON.
    }
    throw new Error(message)
  }

  // Browser and development fallback: preserve the normal download behavior.
  const blobUrl = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(blobUrl), 0)
  return undefined
}
