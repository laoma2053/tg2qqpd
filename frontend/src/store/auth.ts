import { defineStore } from "pinia";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: localStorage.getItem("token") || "",
  }),
  actions: {
    setToken(t: string) {
      this.token = t;
      localStorage.setItem("token", t);
    },
    logout() {
      this.token = "";
      localStorage.removeItem("token");
    },
  },
});
