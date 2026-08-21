import type { Field } from '../types'

// Autosized textareas are capped: an unbounded value (e.g. the Hosts entries)
// would stretch its group card past the viewport, and on tool pages the view
// does not scroll, which pushed the log card out of reach.  Past the cap the
// textarea scrolls internally instead.
const TEXTAREA_MAX_HEIGHT = 400
function fitTextarea(el: HTMLTextAreaElement) {
  if (el.classList.contains('code-input')) { el.style.height = ''; return }
  // 用户手动拖拽过右下角手柄后（dataset.userSized），不再自动重置高度
  if (el.dataset.userSized === '1') return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight + 2, TEXTAREA_MAX_HEIGHT)}px`
}
function resizeTextarea(event: Event) { fitTextarea(event.target as HTMLTextAreaElement) }
export function onTextareaInput(field: Field, event: Event) {
  resizeTextarea(event)
  // Structured textareas (yaml/json) render a transparent <textarea> over a
  // highlighted <pre>; keeping the field model in sync on every input makes
  // the highlight follow the edit live, instead of only after blur/change.
  // For plain textareas this also protects in-progress edits from being
  // reset by a later re-render that re-applies the stale bound value.
  const input = event.target as HTMLTextAreaElement
  if (field.value !== input.value) field.value = input.value
}
// 在右下角 resize 手柄区域按下时标记 userSized，autosize 之后不再覆盖用户手动调整的高度
function watchUserResize(el: HTMLTextAreaElement) {
  el.addEventListener('mousedown', (event: MouseEvent) => {
    const rect = el.getBoundingClientRect()
    if (event.clientX >= rect.right - 16 && event.clientY >= rect.bottom - 16) el.dataset.userSized = '1'
  })
}
export const vAutosize = { mounted: (el: HTMLTextAreaElement) => { fitTextarea(el); watchUserResize(el) }, updated: (el: HTMLTextAreaElement) => fitTextarea(el) }

function escapeHtml(source: string) { return source.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }
function highlightYaml(source: string) {
  return source.split('\n').map(line => {
    const escaped = escapeHtml(line)
    if (/^\s*#/.test(line)) return `<span class="tok-com">${escaped}</span>`
    const match = escaped.match(/^(\s*[\w.-]+)(\s*:)(.*)$/)
    if (!match) return escaped || ' '
    const value = match[3]
      .replace(/('[^']*'|"[^"]*")/g, '<span class="tok-str">$1</span>')
      .replace(/\b(\d[\d.]*)\b/g, '<span class="tok-num">$1</span>')
      .replace(/\b(true|false|null|~)\b/g, '<span class="tok-kw">$1</span>')
    return `<span class="tok-key">${match[1]}</span>${match[2]}${value}`
  }).join('\n')
}
const JSON_TOKEN_PATTERN = /"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g
function highlightJson(source: string) {
  let output = ''
  let offset = 0
  for (const match of source.matchAll(JSON_TOKEN_PATTERN)) {
    const index = match.index ?? 0
    const token = match[0]
    output += escapeHtml(source.slice(offset, index))
    let tokenClass = 'tok-num'
    if (token.startsWith('"')) tokenClass = /^\s*:/.test(source.slice(index + token.length)) ? 'tok-key' : 'tok-str'
    else if (/^(true|false|null)$/.test(token)) tokenClass = 'tok-kw'
    output += `<span class="${tokenClass}">${escapeHtml(token)}</span>`
    offset = index + token.length
  }
  return output + escapeHtml(source.slice(offset))
}
export function isStructuredTextarea(field: Field) { return field.mode === 'yaml' || field.mode === 'json' }
export function highlightTextarea(field: Field) {
  const source = String(field.value ?? '')
  return field.mode === 'json' ? highlightJson(source) : highlightYaml(source)
}
