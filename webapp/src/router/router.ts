import {createRouter, createWebHashHistory} from "vue-router"

import main from '@/components/views/main.vue'

const router = createRouter({
    routes:
        [
            {
                path: '/',
                component: main
            }
        ],
    history: createWebHashHistory()
})

export default router
