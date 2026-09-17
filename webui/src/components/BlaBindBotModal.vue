<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { t } from '../i18n'
import { useBlaBindBotStore } from '../stores/blaBindBot'

const blaBindBot = useBlaBindBotStore()
const { bindOpen, bindBusy, bindLink } = storeToRefs(blaBindBot)
const { submitBind, closeBind } = blaBindBot
</script>

<template>
  <div v-if="bindOpen" class="modal-mask" @click.self="closeBind">
    <div class="modal-card">
      <h3>{{ t('绑定到 Bot') }}</h3>
      <p class="modal-text">{{ t('把当前实例的 Cookie 绑定到机器人，请粘贴机器人在私聊里返回的完整绑定链接。') }}</p>
      <label class="modal-field">{{ t('绑定链接') }}<input v-model="bindLink" placeholder="https://…/bind/…" spellcheck="false" @keyup.enter="submitBind"></label>
      <div class="modal-actions">
        <button class="btn" :disabled="bindBusy" @click="closeBind">{{ t('取消') }}</button>
        <button class="btn primary" :disabled="bindBusy || !bindLink.trim()" @click="submitBind">{{ bindBusy ? t('绑定中…') : t('绑定') }}</button>
      </div>
    </div>
  </div>
</template>
