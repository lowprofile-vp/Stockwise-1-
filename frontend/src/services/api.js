import axios from "axios";
import { accessToken, supabase, supabaseEnabled } from "./supabase";

// When the frontend and API are deployed together on Vercel, leave VITE_API_URL
// unset: production builds call /api on the same site. Set it only to point at an
// API hosted elsewhere. It is accepted with or without a trailing slash or /api.
function resolveApiUrl() {
  const raw = (import.meta.env.VITE_API_URL || "").trim().replace(/\/+$/, "");
  if (!raw) {
    // Deployed together on Vercel, the API lives on the same site at /api.
    return import.meta.env.PROD ? "/api" : "http://localhost:5000/api";
  }
  // A localhost API URL (usually from a local .env that got committed) can never
  // work for visitors of the deployed site, so fall back to the same-site /api.
  const pointsAtLocalhost = /^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/i.test(raw);
  const pageIsLocal = typeof window !== "undefined" && /^(localhost|127\.0\.0\.1)$/i.test(window.location.hostname);
  if (import.meta.env.PROD && pointsAtLocalhost && !pageIsLocal) return "/api";
  return /\/api$/.test(raw) ? raw : `${raw}/api`;
}

export const API_URL = resolveApiUrl();

const api = axios.create({
  baseURL: API_URL,
  timeout: 60000,
});

api.interceptors.request.use(async (config) => {
  const token = await accessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const url = error.config?.url || "";
    const profileMissing = error.response?.data?.details?.code === "profile_required";
    if (status === 401 && !profileMissing && !url.includes("/auth/login") && !url.includes("/auth/register") && !url.includes("/auth/provision")) {
      localStorage.removeItem("stockwise_token");
      sessionStorage.removeItem("stockwise_token");
      if (supabaseEnabled && supabase) supabase.auth.signOut();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.assign("/login");
      }
    }
    return Promise.reject(error);
  },
);

export function errorMessage(error, fallback = "Something went wrong. Please try again.") {
  const data = error?.response?.data;
  if (!data) return error?.message || fallback;
  if (Array.isArray(data.details?.rows) && data.details.rows.length) {
    const rows = data.details.rows.map((row) => `Row ${row.row}: ${row.error}`).join(" ");
    return `${data.error} ${rows}`;
  }
  return data.error || fallback;
}

export default api;