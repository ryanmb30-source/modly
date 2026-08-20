/**
 * Attaching the API token in the renderer (issue #9).
 *
 * The backend rejects any request that cannot present the token, so everything
 * the renderer sends has to carry it. There are two shapes of caller and they
 * need different mechanisms:
 *
 * - Requests the app builds itself (axios, fetch) send the `X-Modly-Token`
 *   header. Preferred: the value stays out of the URL.
 * - URLs handed to a loader -- three.js/drei fetch meshes and splats through
 *   their own machinery and give no way to set a header -- carry it as the
 *   `modly_token` query parameter instead. The backend redacts that parameter
 *   from its access log so the secret is not written to disk on every load.
 *
 * The parameter is `modly_token`, not `token`, because `token` is already taken
 * on the HuggingFace download endpoints.
 */

export const API_TOKEN_HEADER = 'X-Modly-Token'
export const API_TOKEN_QUERY_PARAM = 'modly_token'

export function apiAuthHeaders(apiToken: string): Record<string, string> {
  return apiToken ? { [API_TOKEN_HEADER]: apiToken } : {}
}

/**
 * Appends the token to a URL for a consumer that cannot set headers.
 *
 * Returns the URL untouched when there is no token yet (the backend is still
 * starting), so a caller cannot accidentally send the literal string
 * "undefined" as the credential.
 */
export function withApiToken(url: string, apiToken: string): string {
  if (!apiToken) return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}${API_TOKEN_QUERY_PARAM}=${encodeURIComponent(apiToken)}`
}
