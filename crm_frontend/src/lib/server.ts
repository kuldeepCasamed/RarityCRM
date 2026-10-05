export const API_URL = process.env.CRM_API_URL ?? "http://localhost:8000/api/crm";
// Distinct name: cookies on "localhost" are shared across ports, so a generic name can collide with other local apps.
export const COOKIE = "rarity_crm_token";
