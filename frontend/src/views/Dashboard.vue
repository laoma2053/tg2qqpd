<template>
  <a-row :gutter="16">
    <a-col :span="6">
      <a-card>
        <a-statistic title="队列长度" :value="stats.queue_length" />
      </a-card>
    </a-col>

    <a-col :span="6">
      <a-card>
        <a-statistic title="今日成功" :value="stats.success_today" />
      </a-card>
    </a-col>

    <a-col :span="6">
      <a-card>
        <a-statistic title="今日失败" :value="stats.failed_today" />
      </a-card>
    </a-col>

    <a-col :span="6">
      <a-card>
        <a-statistic title="死信数量" :value="stats.dead_count" />
      </a-card>
    </a-col>
  </a-row>

  <a-divider />

  <a-space>
    <a-button type="link" href="/metrics" target="_blank">
      Prometheus 指标
    </a-button>
    <a-button @click="load">刷新</a-button>
  </a-space>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { fetchSystemStats, SystemStats } from "@/api/system";

const stats = ref<SystemStats>({
  queue_length: 0,
  success_today: 0,
  failed_today: 0,
  dead_count: 0,
});

async function load() {
  const res = await fetchSystemStats();
  stats.value = res.data;
}

onMounted(load);
</script>
