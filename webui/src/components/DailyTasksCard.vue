<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { t } from '../i18n'

defineProps<{ instanceName: string }>()
</script>

<template>
  <div class="dashboard-summary-column daily-tasks-card">
    <div class="dashboard-summary-heading"><span class="dashboard-summary-icon daily"><AppIcon name="calendar" :size="16" /></span><span>{{ t('本日任务') }}</span></div>
    <div class="dashboard-kv"><span>{{ t('企业塔结果') }}</span><strong>--</strong></div>
    <div class="dashboard-kv"><span>{{ t('竞技场胜 / 负') }}</span><strong>-- / --</strong></div>
    <div class="dashboard-kv"><span>{{ t('今日获得石头') }}</span><strong>--</strong></div>
    <div class="dashboard-card-note daily-note">{{ t('每日 04:00 清零') }} / {{ t('数据待接入') }}</div>
  </div>
</template>
