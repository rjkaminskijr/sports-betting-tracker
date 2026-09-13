const TRANSIENT_STATUSES = new Set([502, 503, 504]);

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function messageFrom(data, fallback) {
  const raw = String(data?.error || data?.message || "").trim();
  if (!raw) return fallback;
  if (/gateway timeout|supabase\s+504|\b504\b/i.test(raw)) {
    return "Temporary connection issue. Please try again.";
  }
  return raw;
}

export async function fetchJsonWithRetry(
  url,
  options = {},
  { retries = 1, retryDelayMs = 650, fallbackMessage = "Unable to load data." } = {}
) {
  let lastError = null;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(url, options);
      const text = await response.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = text ? { error: text } : {};
      }

      if (response.ok) return { response, data };

      const transient = TRANSIENT_STATUSES.has(response.status);
      if (transient && attempt < retries) {
        await wait(retryDelayMs);
        continue;
      }

      throw new Error(
        transient
          ? "Temporary connection issue. Please try again."
          : messageFrom(data, fallbackMessage)
      );
    } catch (error) {
      lastError = error;
      const networkError = error instanceof TypeError;
      if (networkError && attempt < retries) {
        await wait(retryDelayMs);
        continue;
      }
      throw error;
    }
  }

  throw lastError || new Error(fallbackMessage);
}
