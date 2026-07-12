export type ApiUrlParams = Record<string, string | number | undefined>;

export function buildApiUrl(
  path: string,
  params: ApiUrlParams | undefined,
  apiBase: string,
  origin: string
): string {
  const configuredBase = apiBase.trim() || "/";
  const baseUrl = new URL(configuredBase, origin);

  if (!baseUrl.pathname.endsWith("/")) {
    baseUrl.pathname = `${baseUrl.pathname}/`;
  }

  const url = new URL(path, baseUrl);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }
  return url.toString();
}
