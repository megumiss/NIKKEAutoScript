export class JsonSocket {
  private socket?: WebSocket
  private stopped = false
  constructor(private path: string, private onMessage: (value: any) => void, private reconnect = true, private onClose?: (event: CloseEvent) => void) {}

  connect() {
    this.stopped = false
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    this.socket = new WebSocket(`${scheme}://${location.host}${this.path}`)
    this.socket.onmessage = event => this.onMessage(JSON.parse(event.data))
    this.socket.onclose = event => {
      this.onClose?.(event)
      if (this.reconnect && !this.stopped) setTimeout(() => this.connect(), 2000)
    }
  }

  // socket 未建立/未打开时静默丢弃，调用方无需关心连接时序
  send(value: any) { if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(value)) }

  close() { this.stopped = true; this.socket?.close() }
}
