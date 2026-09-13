const baseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/, "");
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

export function supabaseConfigured() {
  return Boolean(baseUrl && serviceKey);
}

export async function supabaseRest(
  path,
  { searchParams = {}, cache = "no-store", method = "GET", body, prefer } = {}
) {
  if (!baseUrl || !serviceKey) {
    throw new Error("Supabase environment variables are not configured.");
  }

  const url = new URL(`${baseUrl}/rest/v1/${path}`);
  for (const [key, value] of Object.entries(searchParams)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  const headers = {
    apikey: serviceKey,
    Authorization: `Bearer ${serviceKey}`,
    Accept: "application/json",
    "Content-Type": "application/json"
  };
  if (prefer) headers.Prefer = prefer;

  const response = await fetch(url, {
    cache,
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body)
  });

  if (!response.ok) {
    const responseBody = await response.text();
    throw new Error(`Supabase ${response.status}: ${responseBody}`);
  }

  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export async function invokeTrackerFunction(functionName, body = {}, { timeoutMs = 180000 } = {}) {
  if (!baseUrl) throw new Error("NEXT_PUBLIC_SUPABASE_URL is not configured.");
  const token = process.env.BET_UPLOAD_TOKEN;
  if (!token) {
    throw new Error(
      "BET_UPLOAD_TOKEN is not configured in .env.local. Add the same custom token used by the existing Supabase Edge Functions."
    );
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}/functions/v1/${functionName}`, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "x-upload-token": token
      },
      body: JSON.stringify(body || {})
    });
    const text = await response.text();
    let parsed = {};
    try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = { raw: text }; }
    if (!response.ok) {
      throw new Error(`Edge Function ${functionName} ${response.status}: ${text}`);
    }
    return parsed;
  } finally {
    clearTimeout(timeout);
  }
}
