<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ language: string; ready: boolean; bitrate: number }>()
const emit = defineEmits<{ (e: 'send', message: object): void }>()
const labels: Record<string, [string, string, string]> = {
  clipboard: ['剪贴板', 'Clipboard', 'クリップボード'],
  bitrate: ['视频码率', 'Video bitrate', '映像ビットレート'],
  content: ['文本内容', 'Text', 'テキスト'],
  read_device: ['读取设备', 'Read from device', 'デバイスから読み取り'],
  write_device: ['写入设备', 'Write to device', 'デバイスに書き込み'],
  paste_device: ['粘贴到设备', 'Paste on device', 'デバイスに貼り付け'],
  read_local: ['读取本机', 'Read local clipboard', 'ローカルから読み取り'],
  copy_local: ['复制到本机', 'Copy to local clipboard', 'ローカルにコピー'],
  clipboard_hint: ['可在此输入或粘贴文本；读取设备后可复制到本机。', 'Type or paste text here. Read the device clipboard to copy it locally.', 'テキストを入力・貼り付けできます。デバイスから読み取った内容をローカルにコピーできます。'],
  read_ok: ['已读取设备剪贴板', 'Device clipboard received.', 'デバイスのクリップボードを読み取りました'],
  empty: ['设备剪贴板为空', 'Device clipboard is empty.', 'デバイスのクリップボードは空です'],
  sent: ['已发送到设备', 'Sent to device.', 'デバイスに送信しました'],
  copied: ['已复制到本机', 'Copied to local clipboard.', 'ローカルにコピーしました'],
  local_read_ok: ['已读取本机剪贴板', 'Local clipboard received.', 'ローカルのクリップボードを読み取りました'],
  local_read_failed: ['浏览器无法读取本机剪贴板，请在文本框中手动粘贴。', 'The browser cannot read the local clipboard. Paste into the text box manually.', 'ブラウザーがクリップボードを読み取れません。テキスト欄に手動で貼り付けてください。'],
  local_copy_failed: ['复制失败，请选中文本后手动复制。', 'Copy failed. Select the text and copy it manually.', 'コピーできません。テキストを選択して手動でコピーしてください。'],
  too_long: ['发送文本不能超过 64 KB', 'Text to send must not exceed 64 KB.', '送信テキストは 64 KB 以下にしてください'],
  waiting: ['正在等待设备…', 'Waiting for device…', 'デバイスの応答を待機中…'],
  timeout: ['设备未返回剪贴板内容，可能为空或暂不可读。', 'No clipboard content returned. It may be empty or temporarily unavailable.', 'クリップボードの応答がありません。空か、一時的に読み取れない可能性があります。'],
  write_timeout: ['未收到设备确认，请重试', 'No acknowledgement from the device. Please retry.', 'デバイスから確認が届きません。再試行してください'],
  bitrate_hint: ['0.1–64 Mbps。较低码率可减少带宽占用，较高码率画面更清晰。应用后会保存设置并短暂重连投屏。', '0.1–64 Mbps. Lower values reduce bandwidth; higher values improve clarity. Applying saves the setting and briefly reconnects screen sharing.', '0.1–64 Mbps。低い値は帯域幅を節約し、高い値は画質を改善します。適用すると設定を保存し、画面共有を再接続します。'],
  invalid_bitrate: ['请输入 0.1–64 之间的码率', 'Enter a bitrate between 0.1 and 64.', '0.1～64 の値を入力してください'],
  apply: ['应用并重连', 'Apply and reconnect', '適用して再接続'],
  applying: ['正在应用…', 'Applying…', '適用中…'],
  apply_timeout: ['设置请求超时，请重试', 'Settings request timed out. Please retry.', '設定要求がタイムアウトしました。再試行してください'],
  close: ['关闭', 'Close', '閉じる'],
}
function t(key: string) {
  return labels[key]?.[props.language === 'en-US' ? 1 : props.language === 'ja-JP' ? 2 : 0] || key
}

const dialog = ref<HTMLDialogElement>()
const textArea = ref<HTMLTextAreaElement>()
const rateInput = ref<HTMLInputElement>()
const panel = ref<'clipboard' | 'bitrate' | ''>('')
const text = ref('')
const rate = ref<number | string>(16)
const notice = ref('')
const error = ref(false)
const pending = ref('')
const localBusy = ref(false)
const busy = computed(() => !!pending.value || localBusy.value)
let sequence = 0
let timer: number | undefined
let revision = 0

function feedback(key: string, failed = false) { notice.value = key; error.value = failed }
function clearPending() { pending.value = ''; window.clearTimeout(timer) }
function close() {
  revision++
  clearPending()
  localBusy.value = false
  panel.value = ''
  dialog.value?.close()
}
async function open(kind: 'clipboard' | 'bitrate') {
  panel.value = kind
  feedback('')
  rate.value = props.bitrate / 1000000
  await nextTick()
  dialog.value?.showModal()
  if (kind === 'clipboard') textArea.value?.focus()
  else rateInput.value?.focus()
}
function waitForDevice(kind: string) {
  pending.value = kind
  feedback('waiting')
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    clearPending()
    feedback(kind === 'read' ? 'timeout' : 'write_timeout', true)
  }, 15000)
}
function readDevice() {
  waitForDevice('read')
  emit('send', { type: 'clipboard_get' })
}
function writeDevice(paste = false) {
  if (new TextEncoder().encode(text.value).length > 65536) { feedback('too_long', true); return }
  sequence++
  waitForDevice('write')
  emit('send', { type: 'clipboard_set', text: text.value, sequence, paste })
}
function handleMessage(message: { type: string; text?: string; sequence?: number }) {
  if (message.type === 'clipboard' && pending.value === 'read') {
    clearPending()
    text.value = message.text || ''
    feedback(text.value ? 'read_ok' : 'empty')
  } else if (message.type === 'clipboard_ack' && pending.value === 'write' && message.sequence === sequence) {
    clearPending()
    feedback('sent')
  }
}
async function readLocal() {
  const current = revision
  localBusy.value = true
  try {
    if (!navigator.clipboard?.readText) throw new Error('Clipboard API unavailable')
    const value = await navigator.clipboard.readText()
    if (current !== revision) return
    text.value = value
    feedback('local_read_ok')
  } catch { if (current === revision) feedback('local_read_failed', true) }
  finally { if (current === revision) localBusy.value = false }
}
async function copyLocal() {
  const current = revision
  localBusy.value = true
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text.value)
    else {
      // LAN HTTP may lack the Clipboard API; copy only on this explicit click.
      textArea.value?.focus()
      textArea.value?.select()
      if (!document.execCommand('copy')) throw new Error('Copy failed')
    }
    if (current === revision) feedback('copied')
  } catch { if (current === revision) feedback('local_copy_failed', true) }
  finally { if (current === revision) localBusy.value = false }
}
function applyBitrate() {
  const mbps = Number(rate.value)
  if (!Number.isFinite(mbps) || mbps < 0.1 || mbps > 64) { feedback('invalid_bitrate', true); return }
  pending.value = 'bitrate'
  feedback('applying')
  timer = window.setTimeout(() => { clearPending(); feedback('apply_timeout', true) }, 30000)
  emit('send', { type: 'bitrate', value: Math.round(mbps * 1000000) })
}
watch(() => props.ready, ready => { if (!ready) close() })
onBeforeUnmount(close)
defineExpose({ handleMessage, close })
</script>

<template>
  <button type="button" :title="t('clipboard')" :aria-label="t('clipboard')" :disabled="!ready" @click="open('clipboard')">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H5v17h14V4h-4M9 2h6v5H9zM8 11h8M8 15h8" /></svg>
  </button>
  <button type="button" :title="t('bitrate')" :aria-label="t('bitrate')" :disabled="!ready" @click="open('bitrate')">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /></svg>
  </button>
  <Teleport to="body">
    <dialog ref="dialog" class="scrcpy-dialog" :aria-label="t(panel)" @cancel.prevent="close" @click.self="close">
      <div class="scrcpy-dialog-head"><h3>{{ t(panel) }}</h3><button class="btn sm" type="button" @click="close">{{ t('close') }}</button></div>
      <template v-if="panel === 'clipboard'">
        <label class="scrcpy-field">{{ t('content') }}<textarea ref="textArea" v-model="text" rows="6" :readonly="busy" spellcheck="false"></textarea></label>
        <p class="scrcpy-hint">{{ t('clipboard_hint') }}</p>
        <div class="scrcpy-actions">
          <button class="btn sm" type="button" :disabled="busy || !ready" @click="readDevice">{{ t('read_device') }}</button>
          <button class="btn sm" type="button" :disabled="busy || !ready" @click="writeDevice()">{{ t('write_device') }}</button>
          <button class="btn sm primary" type="button" :disabled="busy || !ready" @click="writeDevice(true)">{{ t('paste_device') }}</button>
        </div>
        <div class="scrcpy-actions">
          <button class="btn sm" type="button" :disabled="busy" @click="readLocal">{{ t('read_local') }}</button>
          <button class="btn sm" type="button" :disabled="busy" @click="copyLocal">{{ t('copy_local') }}</button>
        </div>
      </template>
      <form v-else-if="panel === 'bitrate'" @submit.prevent="applyBitrate">
        <label class="scrcpy-field">{{ t('bitrate') }} (Mbps)<input ref="rateInput" v-model="rate" type="number" min="0.1" max="64" step="any" required :disabled="busy"></label>
        <div class="scrcpy-presets"><button v-for="value in [1, 2, 4, 8, 16, 32]" :key="value" class="btn sm" type="button" :disabled="busy" @click="rate = value">{{ value }}</button></div>
        <p class="scrcpy-hint">{{ t('bitrate_hint') }}</p>
        <button class="btn primary" type="submit" :disabled="busy || !ready">{{ t(busy ? 'applying' : 'apply') }}</button>
      </form>
      <p v-if="notice" class="scrcpy-feedback" :class="{ error }" role="status" aria-live="polite">{{ t(notice) }}</p>
    </dialog>
  </Teleport>
</template>

<style scoped>
/* Restore native dialog centering after the global margin reset. */
.scrcpy-dialog { position:fixed; inset:0; margin:auto; width:min(420px, calc(100vw - 32px)); max-height:85vh; overflow:auto; padding:20px; border:1px solid var(--border); border-radius:12px; background:var(--card); color:var(--text); box-shadow:0 12px 40px #0004; }
.scrcpy-dialog::backdrop { background:#0006; }
.scrcpy-dialog-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
.scrcpy-dialog-head h3 { margin:0; font-size:16px; }
.scrcpy-field { display:flex; flex-direction:column; gap:8px; font-size:13px; }
.scrcpy-field textarea, .scrcpy-field input { box-sizing:border-box; width:100%; min-width:0; padding:9px 10px; border:1px solid var(--border); border-radius:6px; background:var(--card-2); color:var(--text); font:inherit; }
.scrcpy-field textarea { resize:vertical; min-height:100px; }
.scrcpy-dialog :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.scrcpy-hint, .scrcpy-feedback { margin:12px 0; font-size:12px; line-height:1.6; color:var(--text-2); }
.scrcpy-feedback { margin-bottom:0; }
.scrcpy-feedback.error { color:var(--red); }
.scrcpy-actions, .scrcpy-presets { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
</style>
