<script setup lang="ts">
import { storeToRefs } from 'pinia'
import AppIcon from '../components/AppIcon.vue'
import AppSelect from '../components/AppSelect.vue'
import LinkifiedText from '../components/LinkifiedText.vue'
import FieldPriority from '../components/config/FieldPriority.vue'
import SecurityEntryActions from '../components/SecurityEntryActions.vue'
import { t } from '../i18n'
import { onTextInput } from '../utils'
import { useDeployStore } from '../stores/deploy'
import { useModalStore } from '../stores/modal'

const deploy = useDeployStore()
const { deployGroups } = storeToRefs(deploy)
const { saveDeployValue, saveDeployField, toggleDeployMulti } = deploy
const { openResetDeployModal } = useModalStore()
</script>

<template>
  <section class="view">
    <article class="card task-hero">
      <div class="task-icon"><AppIcon name="box" :size="22" /></div>
      <div style="flex:1"><h2>{{ t('部署') }}</h2><div class="sub deploy-warning"><AppIcon name="alert-triangle" :size="14" /> {{ t('修改部署配置可能导致更新失败或程序无法启动，修改需要重启后生效，请谨慎操作。') }}</div></div>
      <button class="btn danger" @click="openResetDeployModal"><AppIcon name="undo" :size="15" /> {{ t('还原默认') }}</button>
    </article>
    <div class="cfg-groups">
      <article v-for="group in deployGroups" :key="group.key" class="card group-card">
        <div class="group-head"><h4>{{ group.name }}</h4></div>
        <div class="group-body">
          <template v-for="field in group.fields" :key="field.key">
            <div class="field" :class="{ 'field-wide': field.wide }">
              <div class="field-label"><label class="fname" :for="`deploy-${field.key}`">{{ field.title }}</label><div v-if="field.help" class="fhelp"><LinkifiedText :text="field.help" /></div><div v-for="hint in field.hints || []" :key="hint.tag" class="deploy-hint"><span class="deploy-hint-tag">{{ hint.tag }}</span><span>{{ hint.text }}</span></div></div>
              <div class="field-control">
                <label v-if="field.widget === 'checkbox'" class="switch"><input :id="`deploy-${field.key}`" type="checkbox" :checked="field.value" :disabled="field.saving || field.entryBusy" @change="saveDeployField(field, $event)"><span class="slider"></span></label>
                <AppSelect v-else-if="field.widget === 'select'" :model-value="field.value" :options="field.options" @change="(value: any) => saveDeployValue(field, value)"/>
                <div v-else-if="field.widget === 'multiselect'" class="deploy-multisel">
                  <label v-for="opt in field.options" :key="opt.value" class="deploy-multi-opt" :class="{ on: (field.value || []).includes(opt.value) }"><input type="checkbox" hidden :checked="(field.value || []).includes(opt.value)" @change="toggleDeployMulti(field, opt.value)">{{ opt.label }}</label>
                </div>
                <FieldPriority v-else-if="field.widget === 'priority'" :value="field.value || ''" :options="field.options" :placeholder="t('添加')" @change="(value: string) => saveDeployValue(field, value)"/>
                <input v-else :id="`deploy-${field.key}`" :type="field.widget === 'number' ? 'number' : 'text'" :value="field.value ?? ''" @input="onTextInput(field, $event)" @change="saveDeployField(field, $event)">
              </div>
            </div>
            <SecurityEntryActions v-if="field.key === 'SecurityEntryEnabled' && field.value === true" :disabled="field.saving" @busy="field.entryBusy = $event" />
          </template>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.switch input { display: block; position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; z-index: 1; cursor: pointer; }
.switch input:focus-visible + .slider { outline: 2px solid var(--accent); outline-offset: 3px; }
.switch input:disabled { cursor: not-allowed; }
</style>
