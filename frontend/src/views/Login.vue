<template>
  <a-card title="TG2QQ 管理后台" style="max-width:360px;margin:100px auto">
    <a-input-password v-model:value="password" placeholder="管理员密码" />
    <a-button type="primary" block style="margin-top:16px" @click="doLogin">
      登录
    </a-button>
  </a-card>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { login } from "../api/auth";
import { useAuthStore } from "../store/auth";
import { useRouter } from "vue-router";

const password = ref("");
const store = useAuthStore();
const router = useRouter();

async function doLogin() {
  const res = await login(password.value);
  store.setToken(res.data.token);
  router.push("/");
}
</script>
