export class JsonSocket {
  private socket?: WebSocket
  private stopped = false
  constructor(private path: string, private onMessage: (value: any) => void) {}

  connect() {
    this.stopped = false
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    this.socket = new WebSocket(`${scheme}://${location.host}${this.path}`)
    this.socket.onmessage = event => this.onMessage(JSON.parse(event.data))
    this.socket.onclose = () => !this.stopped && setTimeout(() => this.connect(), 2000)
  }

  close() { this.stopped = true; this.socket?.close() }
}
