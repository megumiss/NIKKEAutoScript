<template>
  <iframe class="nkas" :src="url"></iframe>
</template>

<script lang="ts">
  import {defineComponent} from 'vue';
  import {webuiUrl} from '../../../main/src/config';
  const ipcRenderer = require('electron').ipcRenderer;

  type PickerMode = 'file' | 'directory';

  interface PickerRequestPayload {
    mode?: PickerMode;
    title?: string;
    defaultPath?: string;
    accept?: string[];
  }

  interface PickerRequestMessage {
    source: string;
    type: string;
    requestId: string;
    payload?: PickerRequestPayload;
  }

  export default defineComponent({
    name: 'NKAS',
    data() {
      return {
        webuiOrigin: new URL(webuiUrl).origin,
      };
    },
    computed: {
      url: function () {
        return webuiUrl;
      },
    },
    mounted() {
      window.addEventListener('message', this.onWebuiMessage);
    },
    beforeUnmount() {
      window.removeEventListener('message', this.onWebuiMessage);
    },
    methods: {
      async onWebuiMessage(event: MessageEvent) {
        if (event.origin !== this.webuiOrigin) return;

        const data = event.data as PickerRequestMessage;
        if (!data || data.source !== 'nkas-webui') return;
        if (data.type !== 'dialog:pick-path:request') return;
        if (!data.requestId || typeof data.requestId !== 'string') return;

        const payload = (data.payload && typeof data.payload === 'object') ? data.payload : {};
        const mode: PickerMode = payload.mode === 'directory' ? 'directory' : 'file';
        const title = typeof payload.title === 'string' ? payload.title : '';
        const defaultPath = typeof payload.defaultPath === 'string' ? payload.defaultPath : '';
        const accept = Array.isArray(payload.accept)
          ? payload.accept.filter((item) => typeof item === 'string').map((item) => String(item))
          : [];

        const responseBase = {
          source: 'nkas-electron',
          type: 'dialog:pick-path:response',
          requestId: data.requestId,
        };
        const source = event.source as MessageEventSource | null;
        const sendResponse = (payload: any) => {
          if (!source || typeof (source as any).postMessage !== 'function') return;
          (source as any).postMessage({
            ...responseBase,
            payload,
          }, event.origin);
        };

        try {
          const result = await ipcRenderer.invoke('dialog:pick-path', {
            mode,
            title,
            defaultPath,
            accept,
          });
          sendResponse(result);
        } catch (error: any) {
          const message = (error && error.message) ? String(error.message) : String(error);
          sendResponse({ ok: false, canceled: false, path: '', error: message });
        }
      },
    },
  });
</script>

<style scoped>
  .nkas {
    border-width: 0;
    width: 100vw;
    height: 100vh;
    overflow: hidden;
  }
</style>
