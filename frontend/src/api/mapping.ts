import request from "./request";

/**
 * 映射数据结构（当前版本）
 * ⚠️ 后续建议改为 tg_chat_id 为主键，更稳定
 */
export interface Mapping {
  id: number;
  tg_channel: string;
  qq_channel: string;
  remark: string;
  gray_ratio: number; // 0 - 100
  enabled: boolean;
}

export function fetchMappings() {
  return request.get<Mapping[]>("/mappings");
}

export function createMapping(data: Partial<Mapping>) {
  return request.post("/mappings", data);
}

export function updateMapping(id: number, data: Partial<Mapping>) {
  return request.put(`/mappings/${id}`, data);
}

export function deleteMapping(id: number) {
  return request.delete(`/mappings/${id}`);
}
