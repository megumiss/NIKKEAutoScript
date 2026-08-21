<script setup lang="ts">
import { storeToRefs } from 'pinia'
import AppSelect from './AppSelect.vue'
import { t } from '../i18n'
import { useModalStore } from '../stores/modal'

const modalStore = useModalStore()
const { modal, originOptions, deployTemplateOptions, modalConfirmMessage, modalAlertTitle, modalAlertMessage } = storeToRefs(modalStore)
const { confirmModal } = modalStore
</script>

<template>
  <div v-if="modal.type" class="modal-mask" @click.self="modal.type = ''">
    <div class="modal-card">
      <h3>{{ modal.type === 'create' ? t('新建实例') : modal.type === 'rename' ? t('重命名') : modal.type === 'resetDeploy' ? t('还原默认') : modal.type === 'confirm' ? t('确认') : modal.type === 'alert' ? modalAlertTitle : t('删除') }}</h3>
      <template v-if="modal.type === 'create'">
        <label class="modal-field">{{ t('名称') }}<input v-model="modal.name" @keyup.enter="confirmModal"></label>
        <label class="modal-field">{{ t('复制来源实例') }}<AppSelect v-model="modal.origin" :options="originOptions"/></label>
      </template>
      <template v-else-if="modal.type === 'rename'">
        <label class="modal-field">{{ t('名称') }}<input v-model="modal.name" @keyup.enter="confirmModal"></label>
      </template>
      <template v-else-if="modal.type === 'resetDeploy'">
        <p class="modal-text">{{ t('将全部部署配置还原为默认值？') }}{{ t('此操作不可恢复。') }}</p>
        <label class="modal-field">{{ t('模板') }}<AppSelect v-model="modal.template" :options="deployTemplateOptions"/></label>
      </template>
      <p v-else-if="modal.type === 'confirm'" class="modal-text">{{ modalConfirmMessage }}</p>
      <p v-else-if="modal.type === 'alert'" class="modal-text">{{ modalAlertMessage }}</p>
      <p v-else class="modal-text">{{ t('删除') }} {{ modal.name }}？{{ t('此操作不可恢复。') }}</p>
      <div class="modal-actions">
        <button v-if="modal.type !== 'alert'" class="btn" @click="modal.type = ''">{{ t('取消') }}</button>
        <button class="btn" :class="modal.type === 'delete' || modal.type === 'resetDeploy' ? 'danger' : 'primary'" :disabled="modal.busy || ((modal.type === 'create' || modal.type === 'rename') && !modal.name.trim())" @click="confirmModal">{{ modal.type === 'alert' ? t('知道了') : t('确定') }}</button>
      </div>
    </div>
  </div>
</template>
