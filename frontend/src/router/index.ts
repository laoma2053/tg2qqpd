import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../store/auth";

/**
 * 路由表
 * - /login：独立页面（不使用 Layout）
 * - 其他后台页面：统一使用 AdminLayout
 */
const routes = [
  {
    path: "/login",
    component: () => import("../views/Login.vue"),
  },
  {
    path: "/",
    component: () => import("../layouts/AdminLayout.vue"),
    children: [
      {
        path: "",
        component: () => import("../views/Dashboard.vue"),
      },
      {
        path: "mapping",
        component: () => import("../views/Mapping.vue"),
      },
      {
        path: "dead",
        component: () => import("../views/DeadLetters.vue"),
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

/**
 * 全局路由守卫
 * - 未登录：只能访问 /login
 * - 已登录：禁止再访问 /login
 */
router.beforeEach((to) => {
  const store = useAuthStore();

  if (!store.token && to.path !== "/login") {
    return "/login";
  }

  if (store.token && to.path === "/login") {
    return "/";
  }

  return true;
});

export default router;
