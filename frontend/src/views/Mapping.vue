<template>
  <a-card title="TG → QQ 映射管理" :bordered="false">
    <!-- 操作栏 -->
    <a-space style="margin-bottom: 16px">
      <a-button type="primary" @click="openCreate">新增映射</a-button>
      <a-button @click="loadData">刷新</a-button>
    </a-space>

    <!-- 映射表格 -->
    <a-table
      rowKey="id"
      :columns="columns"
      :dataSource="list"
      :loading="loading"
      bordered
    >
      <template #bodyCell="{ column, record }">
        <!-- 启用开关 -->
        <template v-if="column.key === 'enabled'">
          <a-switch
            :checked="record.enabled"
            @change="val => onToggle(record, val)"
          />
        </template>

        <!-- 灰度比例 -->
        <template v-if="column.key === 'gray_ratio'">
          {{ record.gray_ratio }}%
        </template>

        <!-- 操作 -->
        <template v-if="column.key === 'action'">
          <a-space>
            <a @click="openEdit(record)">编辑</a>
            <a-popconfirm
              title="确认删除？"
              @confirm="onDelete(record.id)"
            >
              <a style="color:red">删除</a>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 新增 / 编辑弹窗 -->
    <a-modal
      v-model:open="modalVisible"
      :title="editing ? '编辑映射' : '新增映射'"
      @ok="onSubmit"
    >
      <a-form :model="form" layout="vertical">
        <a-form-item label="TG 频道">
          <a-input v-model:value="form.tg_channel" placeholder="@channel" />
        </a-form-item>

        <a-form-item label="QQ 频道 ID">
          <a-input v-model:value="form.qq_channel" />
        </a-form-item>

        <a-form-item label="备注">
          <a-input v-model:value="form.remark" />
        </a-form-item>

        <a-form-item label="灰度比例">
          <a-slider v-model:value="form.gray_ratio" :min="0" :max="100" />
        </a-form-item>

        <a-form-item label="是否启用">
          <a-switch v-model:checked="form.enabled" />
        </a-form-item>
      </a-form>
    </a-modal>
  </a-card>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import {
  fetchMappings,
  createMapping,
  updateMapping,
  deleteMapping,
  Mapping,
} from "@/api/mapping";

const list = ref<Mapping[]>([]);
const loading = ref(false);

const modalVisible = ref(false);
const editing = ref(false);
const currentId = ref<number | null>(null);

/**
 * 表单模型
 */
const form = ref<Partial<Mapping>>({
  tg_channel: "",
  qq_channel: "",
  remark: "",
  gray_ratio: 100,
  enabled: true,
});

/**
 * 表格列定义
 */
const columns = [
  { title: "TG 频道", dataIndex: "tg_channel" },
  { title: "QQ 频道", dataIndex: "qq_channel" },
  { title: "备注", dataIndex: "remark" },
  { title: "灰度", key: "gray_ratio" },
  { title: "启用", key: "enabled" },
  { title: "操作", key: "action" },
];

/**
 * 加载数据
 */
async function loadData() {
  loading.value = true;
  const res = await fetchMappings();
  list.value = res.data;
  loading.value = false;
}

onMounted(loadData);

/**
 * 新增
 */
function openCreate() {
  editing.value = false;
  currentId.value = null;
  form.value = {
    tg_channel: "",
    qq_channel: "",
    remark: "",
    gray_ratio: 100,
    enabled: true,
  };
  modalVisible.value = true;
}

/**
 * 编辑
 */
function openEdit(record: Mapping) {
  editing.value = true;
  currentId.value = record.id;
  form.value = { ...record };
  modalVisible.value = true;
}

/**
 * 提*
