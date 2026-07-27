<script setup lang="ts">
defineProps<{ value: any; disabled?: boolean }>()
const emit = defineEmits<{ clear: [] }>()
</script>

<template>
  <div class="special-field storage-field">
    <pre>{{ JSON.stringify(value || {}, null, 2) }}</pre>
    <button type="button" class="btn danger sm" :disabled="disabled || !Object.keys(value || {}).length" @click="emit('clear')">清空记录</button>
  </div>
</template>
