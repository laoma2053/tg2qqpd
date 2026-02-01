<template>
  <a-layout style="min-height: 100vh">
    <!-- 左侧菜单 -->
    <a-layout-sider collapsible>
      <div class="logo">TG → QQ Admin</div>

      <a-menu
        theme="dark"
        mode="inline"
        :selectedKeys="[activeKey]"
        @click="onMenuClick"
      >
        <a-menu-item key="/">
          Dashboard
        </a-menu-item>
        <a-menu-item key="/mapping">
          映射管理
        </a-menu-item>
        <a-menu-item key="/dead">
          死信队列
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <!-- 右侧内容 -->
    <a-layout>
      <a-layout-header class="header">
        <span />
        <a-button type="link" @click="logout">退出</a-button>
      </a-layout-header>

      <a-layout-content class="content">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/store/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const activeKey = computed(() => route.path);

function onMenuClick({ key }: { key: string }) {
  router.push(key);
}

function logout() {
  auth.logout();
  router.push("/login");
}
</script>

<style scoped>
.logo {
  height: 48px;
  color: #fff;
  text-align: center;
  line-height: 48px;
  font-weight: bold;
}

.header {
  background: #fff;
  display: flex;
  justify-content: flex-end;
  padding: 0 16px;
}

.content {
  margin: 16px;
  background: #fff;
  padding: 16px;
}
</style>
