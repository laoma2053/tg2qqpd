import request from "./request";

export function login(password: string) {
  // 对应后端：POST /api/login
  return request.post("/login", { password });
}
