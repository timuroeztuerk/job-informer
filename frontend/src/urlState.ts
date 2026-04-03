export type QueryParamValue = string | null | undefined;

const readSearchParams = (): URLSearchParams =>
  typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search);

export const readTextParam = (key: string): string => readSearchParams().get(key) || "";

export const readEnumParam = <T extends string>(key: string, values: readonly T[]): T | "" => {
  const value = readSearchParams().get(key);
  return value && values.includes(value as T) ? (value as T) : "";
};

export const readPositiveIntegerParam = (key: string, fallback: number): number => {
  const value = Number.parseInt(readSearchParams().get(key) || "", 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

export const replaceSearchParams = (updates: Record<string, QueryParamValue>): void => {
  if (typeof window === "undefined") {
    return;
  }

  const url = new URL(window.location.href);

  Object.entries(updates).forEach(([key, value]) => {
    const nextValue = typeof value === "string" ? value.trim() : value;
    if (nextValue) {
      url.searchParams.set(key, nextValue);
    } else {
      url.searchParams.delete(key);
    }
  });

  window.history.replaceState({}, "", url);
};
