import request from "./request";

export interface SystemStats {
  queue_length: number;
  success_today: number;
  failed_today: number;
  dead_count: number;
}

export function fetchSystemStats() {
  return request.get<SystemStats>("/system/stats");
}
