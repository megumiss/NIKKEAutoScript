import { checkEntry, entryRequired } from './security'

export class JsonSocket {
  private socket?: WebSocket
  private stopped = false
  private timer?: ReturnType<typeof setTimeout>
  constructor(private path: string, private onMessage: (value: any) => void, private reconnect = true, private onClose?: (event: CloseEvent) => void) {}

  connect() {
    if (entryRequired.value) return
    this.stopped = false
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    this.socket = new WebSocket(`${scheme}://${location.host}${this.path}`)
    this.socket.onmessage = event => this.onMessage(JSON.parse(event.data))
    this.socket.onclose = async event => {
      this.onClose?.(event)
      if (this.stopped) return
      // A denied handshake is reported as 1006 by browsers, so inspect the
      // anonymous status endpoint before attempting another authenticated WS.
      const authorized = await checkEntry().catch(() => true)
      if (authorized && this.reconnect && !this.stopped) this.timer = setTimeout(() => this.connect(), 2000)
    }
  }

  // socket 未建立/未打开时静默丢弃，调用方无需关心连接时序
  send(value: any) { if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(value)) }

  close() { this.stopped = true; clearTimeout(this.timer); this.socket?.close() }
}
