<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import { api } from '../../api/client'
import { t } from '../../i18n'
import { useToastStore } from '../../stores/toast'

type DropStats = {
  today: string
  days: Record<string, { recorded: number; override: number | null }>
}
const props = defineProps<{ stats: DropStats; endpoint: string }>()
const emit = defineEmits<{ updated: [stats: DropStats] }>()
const toast = useToastStore()
const dialog = ref<HTMLDialogElement>()
const day = ref('')
const count = ref('')
const saving = ref(false)
const error = ref('')
const entry = computed(() => props.stats.days?.[day.value])
const validDay = computed(() => /^\d{4}-\d{2}-\d{2}$/.test(day.value) && day.value <= props.stats.today)
const validCount = computed(() => /^\d+$/.test(count.value) && Number.isSafeInteger(Number(count.value)))

/** 从选中日期回填总数；无记录时保留空输入，避免把未填写的数量误存为零。 */
function resetDraft() {
  count.value = String(entry.value?.override ?? entry.value?.recorded ?? '')
  error.value = ''
}
watch(day, resetDraft)

/** 每次打开都重新读取当前统计，取消过的输入不会成为下一次编辑的默认值。 */
function openEditor() {
  if (!day.value || !validDay.value) day.value = props.stats.today
  resetDraft()
  dialog.value?.showModal()
}

/** 取消不写入统计；请求期间阻止关闭，以便失败后继续编辑当前草稿。 */
function cancelEditor() {
  if (!saving.value) dialog.value?.close()
}

/** 直接按自然日保存，不依赖当天是否有 CSV 记录；null 恢复自动统计，零是有效数量。 */
async function saveDay(restore: boolean) {
  if (saving.value || !validDay.value || (restore ? entry.value?.override == null : !validCount.value)) return
  saving.value = true
  error.value = ''
  try {
    const stats: DropStats = await api.patch(props.endpoint, { date: day.value, total: restore ? null : Number(count.value) })
    emit('updated', stats)
    dialog.value?.close()
    toast.notify(t(restore ? '已恢复自动统计' : '已保存'))
  } catch (exception: unknown) {
    error.value = exception instanceof Error ? exception.message : t('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <button type="button" class="btn sm" aria-haspopup="dialog" @click="openEditor"><AppIcon name="edit" :size="14" /> {{ t('修改每日掉落') }}</button>
  <Teleport to="body">
    <dialog ref="dialog" class="drop-dialog" aria-labelledby="drop-editor-title" @cancel.prevent="cancelEditor" @click.self="cancelEditor">
      <form class="modal-card" :aria-busy="saving" @submit.prevent="saveDay(false)">
        <h3 id="drop-editor-title">{{ t('修改每日掉落') }}</h3>
        <label class="modal-field">{{ t('日期') }}
          <input v-model="day" type="date" :max="stats.today" required autofocus :disabled="saving">
        </label>
        <p class="modal-hint" role="status">
          <template v-if="!validDay">{{ t('请选择今天或更早的日期。') }}</template>
          <template v-else-if="!entry">{{ t('当天没有掉落记录，填写总数即可补录。') }}</template>
          <template v-else>{{ t('自动统计') }}: {{ entry.recorded }}<template v-if="entry.override != null"> · {{ t('已自定义') }}: {{ entry.override }}</template></template>
        </p>
        <label class="modal-field">{{ t('当天掉落总数') }}
          <input v-model="count" type="text" inputmode="numeric" pattern="[0-9]+" required autocomplete="off" :disabled="saving || !validDay" :aria-invalid="Boolean(count) && !validCount">
        </label>
        <p v-if="count && !validCount" class="modal-hint" role="status">{{ t('请输入非负整数。') }}</p>
        <p class="modal-hint">{{ t('保存后，该日期按自定义总数统计，不再累加新记录。恢复自动统计可重新使用所有原始记录。') }}</p>
        <p v-if="error" class="modal-text" role="alert">{{ error }}</p>
        <div class="modal-actions">
          <button v-if="entry?.override != null" type="button" class="btn" :disabled="saving" @click="saveDay(true)">{{ t('恢复自动统计') }}</button>
          <button type="button" class="btn" :disabled="saving" @click="cancelEditor">{{ t('取消') }}</button>
          <button type="submit" class="btn primary" :disabled="saving || !validDay || !validCount">{{ t(saving ? '保存中…' : '保存') }}</button>
        </div>
      </form>
    </dialog>
  </Teleport>
</template>

<style scoped>
.drop-dialog { margin: auto; border: 0; background: transparent; color: var(--text); overflow: visible; }
.drop-dialog::backdrop { background: rgba(0, 0, 0, .45); }
.modal-actions { flex-wrap: wrap; }
</style>
