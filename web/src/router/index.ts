import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layout/AppLayout.vue'),
      redirect: '/home',
      children: [
        { path: 'home', name: 'home', component: () => import('@/views/SearchHome.vue') },
        { path: 'search', name: 'search', component: () => import('@/views/SearchResults.vue') },
        { path: 'knowledge', name: 'knowledge', component: () => import('@/views/KnowledgeBase.vue') },
        {
          path: 'knowledge/:kbId/documents',
          name: 'documents',
          component: () => import('@/views/Document.vue'),
          props: true,
        },
        {
          path: 'knowledge/:kbId/documents/:docId',
          name: 'doc-detail',
          component: () => import('@/views/DocumentDetail.vue'),
          props: true,
        },
        { path: 'history', name: 'history', component: () => import('@/views/History.vue') },
        { path: 'favorites', name: 'favorites', component: () => import('@/views/Favorites.vue') },
        { path: 'settings', name: 'settings', component: () => import('@/views/Setting.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/home' },
  ],
})

router.beforeEach((to) => {
  const user = useUserStore()
  if (!to.meta.public && !user.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.path === '/login' && user.isLoggedIn) {
    return { path: '/home' }
  }
})

export default router
