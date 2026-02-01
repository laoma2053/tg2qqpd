<template>
  <a-card title="死信队列" :bordered="false">
    <a-space style="margin-bottom: 16px">
      <a-button @click="load">刷新</a-button>

      <a-popconfirm
        title="确认批量重放选中的死信？"
        ok-text="确认"
        cancel-text="取消"
        @confirm="onBatchRetry"
      >
        <a-button type="primary" :disabled="selectedRowKeys.length === 0">
          批量重放（{{ selectedRowKeys.length }}）
        </a-button>
      </a-popconfirm>
    </a-space>

    <a-table
      rowKey="id"
      :columns="columns"
      :dataSource="list"
      :loading="loading"
      :row-selection="rowSelection"
      bordered
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'action'">
          <a-popconfirm
            title="确认重放该死信？"
            ok-text="确认"
            cancel-text="取消"
            @confirm="() => onRetry(record.id)"
          >
            <a>重放</a>
          </a-popconfirm>
        </template>
      </template>
    </a-table>
  </a-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { message } from "ant-design-vue";
import {
  fetchDeadLetters,
  retryDeadLetter,
  retryDeadLetters,
  DeadLetter,
} from "@/api/deadletters";

const list = ref<DeadLetter[]>([]);
const loading = ref(false);
const selectedRowKeys = ref<number[]>([]);

const columns = [
  { title: "ID", dataIndex: "id" },
  { title: "TG Chat", dataIndex: "tg_chat_id" },
  { title: "Msg ID", dataIndex: "tg_msg_id" },
  { title: "频道名", dataIndex: "channel_name" },
  { title: "内容预览", dataIndex: "content" },
  { title: "错误", dataIndex: "error" },
  { title: "时间", dataIndex: "created_at" },
  { title: "操作", key: "action" },
];

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (string | number)[]) => {
    selectedRowKeys.value = keys.map((k) => Number(k));
  },
}));

async function load() {
  loading.value = true;
  try {
    const res = await fetchDeadLetters();
    list.value = res.data;
  } finally {
    loading.value = false;
  }
}

async function onRetry(id: number) {
  await retryDeadLetter(id);
  message.success("已重放");
  selectedRowKeys.value = selectedRowKeys.value.filter((x) => x !== id);
  await load();
}

async function onBatchRetry() {
  const ids = selectedRowKeys.value;
  if (!ids.length) return;

  await retryDeadLetters(ids);
  message.success(`已批量重放 ${ids.length} 条`);
  selectedRowKeys.value = [];
  await load();
}

onMounted(load);
</script>
