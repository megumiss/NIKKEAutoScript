<script setup lang="ts">
defineProps<{ data: any; loading?: boolean }>()

function count(value: any) {
  const number = Number(String(value ?? '').replace(/,/g, ''))
  return Number.isFinite(number) ? number.toLocaleString() : (value || '—')
}
</script>

<template>
  <div class="special-field warehouse-field">
    <div v-if="loading" class="special-empty">正在读取仓库记录…</div>
    <div v-else-if="!data?.groups?.length" class="special-empty">暂无仓库物品记录。</div>
    <section v-for="group in data?.groups || []" :key="group.name" class="warehouse-group">
      <header><strong>{{ group.name || '物品' }}</strong><span>{{ group.items?.length || 0 }}</span></header>
      <div class="warehouse-grid">
        <article v-for="item in group.items || []" :key="item.id || item.name" class="warehouse-item">
          <img v-if="item.icon" :src="item.icon" :alt="item.display_name || item.name" loading="lazy">
          <span v-else class="warehouse-fallback">—</span>
          <div><div>{{ item.display_name || item.name || item.id }}</div><small>持有 <b>{{ count(item.count) }}</b></small></div>
        </article>
      </div>
    </section>
    <small v-if="data?.updated_at" class="special-updated">更新于 {{ data.updated_at }}</small>
  </div>
</template>
