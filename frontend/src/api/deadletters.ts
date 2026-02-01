import request from "./auth";

export interface DeadLetter {
  id: number;
  tg_chat_id: number;
  tg_msg_id: number;
  error: string;
  content: string;
  created_at: string;
  qq_channel_id?: string;
  channel_name?: string;
}

export function fetchDeadLetters() {
  return request.get<DeadLetter[]>("/api/deadletters");
}

export function retryDeadLetter(id: number) {
  return request.post(`/api/deadletters/${id}/retry`);
}

export function retryDeadLetters(ids: number[]) {
  return request.post(`/api/deadletters/retry`, { ids });
}
