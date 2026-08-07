<script setup lang="ts">
import { computed } from 'vue'
import AppSelect from '../AppSelect.vue'

// Ordered multi-select for `>`-joined priority strings.  The stored value
// keeps its original "A > B > C" format; chips show the selection in
// priority order (1 = highest) and only the joining is done here.
const props = defineProps<{
  value: string
  options: any[]
  disabled?: boolean
  placeholder?: string
}>()
const emit = defineEmits(['change'])

function valueOf(option: any) { return option !== null && typeof option === 'object' ? option.value ?? option : option }
function labelOf(option: any) { return option !== null && typeof option === 'object' ? option.label ?? String(option.value ?? '') : String(option) }

const selected = computed(() => String(props.value ?? '').split('>').map(item => item.trim()).filter(Boolean))
const labels = computed(() => {
  const map: Record<string, string> = {}
  ;(props.options || []).forEach(option => { map[String(valueOf(option))] = labelOf(option) })
  return map
})
const remaining = computed(() => (props.options || []).filter(option => !selected.value.includes(String(valueOf(option)))))

function update(list: string[]) { emit('change', list.join(' > ')) }
function add(token: any) {
  if (token === undefined || token === null || token === '') return
  update([...selected.value, String(token)])
}
function remove(index: number) { update(selected.value.filter((_, i) => i !== index)) }
function move(index: number, offset: number) {
  const target = index + offset
  const list = [...selected.value]
  if (target < 0 || target >= list.length) return
  ;[list[index], list[target]] = [list[target], list[index]]
  update(list)
}
</script>

<template>
  <div class="priority-field" :class="{ disabled }">
    <div v-if="selected.length" class="priority-chips">
      <span v-for="(token, index) in selected" :key="token" class="priority-chip">
        <span class="priority-num">{{ index + 1 }}</span>
        <span class="priority-label">{{ labels[token] || token }}</span>
        <template v-if="!disabled">
          <button type="button" class="priority-move" :disabled="index === 0" @click="move(index, -1)">‹</button>
          <button type="button" class="priority-move" :disabled="index === selected.length - 1" @click="move(index, 1)">›</button>
          <button type="button" class="priority-remove" @click="remove(index)">✕</button>
        </template>
      </span>
    </div>
    <AppSelect v-if="!disabled && remaining.length" class="priority-add" :model-value="''" :options="remaining" :placeholder="placeholder || '＋'" @change="add"/>
  </div>
</template>
