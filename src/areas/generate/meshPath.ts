/**
 * Turning a viewer `outputUrl` back into a path the API accepts (issue #10).
 *
 * A mesh reaches the viewer by one of two routes, and they carry different
 * shapes of URL:
 *
 * - Generated into the workspace  -> `/workspace/<collection>/<file>.glb`
 * - Imported from anywhere on disk -> `/optimize/serve-file?path=<encoded>`
 *
 * Endpoints that take a `path` want the underlying path, not the URL. Assuming
 * the first shape and stripping `/workspace/` silently no-ops on the second,
 * so the whole serve-file URL gets sent as `path` and the backend rejects it
 * with `400 Invalid path`. That was live for OBJ/STL/PLY export of any
 * imported mesh.
 *
 * Extracted from GeneratePage so the rule lives in one testable place rather
 * than being re-derived at each call site -- which is how export came to have
 * its own, wrong, copy while the optimize and smooth call sites were correct.
 */

const WORKSPACE_PREFIX = '/workspace/'
const SERVE_FILE_PREFIX = '/optimize/serve-file?path='

export function toApiMeshPath(url: string): string {
  if (url.startsWith(WORKSPACE_PREFIX)) {
    return url.slice(WORKSPACE_PREFIX.length)
  }
  if (url.startsWith(SERVE_FILE_PREFIX)) {
    return decodeURIComponent(url.slice(SERVE_FILE_PREFIX.length))
  }
  return url
}

/**
 * The `/optimize/export` URL for a mesh, minus the auth token.
 *
 * A function rather than an inline template in the component so the conversion
 * that was wrong is reachable from a test: the defect was not in `toApiMeshPath`
 * (which was always correct) but in the export call site failing to use it.
 * Callers add the token with `withApiToken`.
 */
export function buildMeshExportUrl(apiUrl: string, outputUrl: string, format: string): string {
  const path = encodeURIComponent(toApiMeshPath(outputUrl))
  return `${apiUrl}/optimize/export?path=${path}&format=${format}`
}
