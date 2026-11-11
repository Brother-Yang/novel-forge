import { http, markSessionChanged } from "./client";

interface SessionStatus {
  enabled: boolean;
  authenticated: boolean;
}

export const authApi = {
  session: () => http.get<SessionStatus>("/auth/session"),
  login: async (password: string) => {
    await http.post("/auth/login", { password });
    markSessionChanged();
  },
  logout: async () => {
    await http.post("/auth/logout");
    markSessionChanged();
  },
};
