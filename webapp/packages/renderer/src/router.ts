import {createRouter, createWebHashHistory} from 'vue-router';
import NKAS from '/@/components/NKAS.vue';

const routes = [
  {path: '/', name: 'NKAS', component: NKAS},
];

export default createRouter({
  routes,
  history: createWebHashHistory(),
});
