<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { t } from '../i18n'

defineProps<{ instanceName: string }>()
</script>

<template>
  <div class="dashboard-summary-column account-info-card">
    <div class="dashboard-summary-heading"><span class="dashboard-summary-icon account"><AppIcon name="chart-square" :size="16" /></span><span>{{ t('账号信息') }}</span></div>
    <div class="dashboard-kv"><span>{{ t('指挥官等级') }}</span><strong>--</strong></div>
    <div class="dashboard-kv"><span>{{ t('无限之塔') }}</span><strong>--</strong></div>
    <div class="dashboard-kv"><span>{{ t('企业塔进度') }}</span><strong>--</strong></div>
    <div class="dashboard-card-note">{{ t('数据待接入') }}</div>
  </div>
</template>
