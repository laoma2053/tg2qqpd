import axios from "axios";
import type { AxiosInstance } from "axios";
import { useAuthStore } from "../store/auth";

/**
 * 统一的 HTTP Client
 * - baseURL: /api （配合 Vite proxy 或 Nginx 反代）
 * - 自动携带 JWT
 */
const request: AxiosInstance = axios.create({
  baseURL: "/api",
  timeout: 15000,
});

request.interceptors.request.use((config) => {
  const store = useAuthStore();

  // Axios v1 中 headers 可能是 undefined，需要保证存在
  config.headers = config.headers ?? {};

  if (store.token) {
    // FastAPI + HTTPBearer 默认格式：Authorization: Bearer <token>
    config.headers.Authorization = `Bearer ${store.token}`;
  }
  return config;
});

export default request;
