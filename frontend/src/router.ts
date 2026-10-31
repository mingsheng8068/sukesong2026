import { createRouter, createWebHistory } from 'vue-router'
import Overview from './views/Overview.vue'
import Events from './views/Events.vue'
import EventDetail from './views/EventDetail.vue'
import Orders from './views/Orders.vue'
import ModuleHome from './views/ModuleHome.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/cockpit' },
    { path: '/cockpit', component: Overview },
    { path: '/overview', redirect: '/cockpit' },
    { path: '/monitoring', component: ModuleHome, meta: { key: 'monitoring' } },
    { path: '/alerts', component: Events, meta: { key: 'alerts' } },
    { path: '/diagnosis', component: ModuleHome, meta: { key: 'diagnosis' } },
    { path: '/events/:id', component: EventDetail },
    { path: '/maintenance', component: Orders, meta: { key: 'maintenance' } },
    { path: '/messages', component: ModuleHome, meta: { key: 'messages' } },
    { path: '/knowledge', component: ModuleHome, meta: { key: 'knowledge' } },
  ],
})
