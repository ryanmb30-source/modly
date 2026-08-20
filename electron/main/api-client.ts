/**
 * The main process's authenticated channel to the local API (issue #9).
 *
 * Every call from Electron to the backend goes through this instance so the
 * token is attached in one place. Calling `axios` directly with an absolute
 * `API_BASE_URL` would compile and then 401 at runtime, which is why
 * ipc-handlers.test.mjs asserts that no such call sites remain.
 *
 * Deliberately a dedicated instance rather than a default on the global axios:
 * this process also talks to github.com and huggingface.co, and a global
 * default header would send Modly's token to both.
 */
import axios from 'axios'

import { API_BASE_URL, API_TOKEN_HEADER, getApiToken } from './python-bridge'

export const apiClient = axios.create({ baseURL: API_BASE_URL })

apiClient.interceptors.request.use((config) => {
  config.headers.set(API_TOKEN_HEADER, getApiToken())
  return config
})

/**
 * Token as a query parameter, for URLs consumed by something that cannot set
 * headers. Prefer the header; this exists for the 3D loaders.
 */
export function withApiToken(pathAndQuery: string): string {
  const separator = pathAndQuery.includes('?') ? '&' : '?'
  return `${pathAndQuery}${separator}modly_token=${encodeURIComponent(getApiToken())}`
}
