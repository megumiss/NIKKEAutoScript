import {createApp} from 'vue'
import 'normalize.css'
import '@/assets/css/common.css'
import router from "@/router/router";
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'

const app = createApp(App)
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(key, component)
}
app.use(router)
app.mount('#app')
