import { isAbsolute, join, relative, resolve as resolvePath } from 'node:path'

const EXTENSION_ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/

export function assertSafeExtensionId(extensionId: unknown): string {
  if (typeof extensionId !== 'string') {
    throw new Error('Extension id must be a string')
  }

  const trimmed = extensionId.trim()
  if (!trimmed) {
    throw new Error('Extension id must not be empty')
  }

  if (trimmed === '.' || trimmed === '..') {
    throw new Error(`Extension id "${extensionId}" is invalid`)
  }

  if (isAbsolute(trimmed)) {
    throw new Error(`Extension id "${extensionId}" must not be an absolute path`)
  }

  if (trimmed.includes('/') || trimmed.includes('\\')) {
    throw new Error(`Extension id "${extensionId}" must not contain path separators`)
  }

  if (!EXTENSION_ID_PATTERN.test(trimmed)) {
    throw new Error(`Extension id "${extensionId}" must match ${EXTENSION_ID_PATTERN}`)
  }

  return trimmed
}

export function resolvePathWithinRoot(rootDir: string, unsafeLeaf: string): string {
  const resolvedRoot = resolvePath(rootDir)
  const resolvedCandidate = resolvePath(resolvedRoot, unsafeLeaf)
  const normalizedRelative = relative(resolvedRoot, resolvedCandidate).replace(/\\/g, '/')

  // An empty relative path means the leaf resolved to the root itself ('', '.')
  // — never a valid child, and catastrophic for deletion call sites.
  if (normalizedRelative === '' || normalizedRelative === '..' || normalizedRelative.startsWith('../') || isAbsolute(normalizedRelative)) {
    throw new Error(`Resolved path escapes root: ${unsafeLeaf}`)
  }

  return resolvedCandidate
}

export function resolveExtensionPathWithinRoot(rootDir: string, extensionId: unknown): string {
  return resolvePathWithinRoot(rootDir, assertSafeExtensionId(extensionId))
}

/**
 * May this URL be handed to shell.openExternal?
 *
 * openExternal passes the string to the OS, which resolves it against the
 * registered protocol handlers. That is the intent for a documentation link and
 * a problem for anything else: on Windows a `file:` URL opens a local file, and
 * custom schemes launch whatever application claims them.
 *
 * Allowlisted rather than denylisted -- the set of schemes an OS will act on is
 * open-ended and grows with installed software, so enumerating the bad ones is
 * a losing game.
 */
const OPENABLE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

export function isAllowedExternalUrl(url: unknown): boolean {
  if (typeof url !== 'string' || url.trim() === '') return false
  try {
    return OPENABLE_PROTOCOLS.has(new URL(url).protocol)
  } catch {
    return false
  }
}

/**
 * Is `candidatePath` the root itself, or somewhere inside it?
 *
 * For call sites handed an already-absolute path that must decide whether it
 * falls under a permitted root -- as opposed to resolvePathWithinRoot, which
 * builds a child path from an untrusted leaf.
 *
 * The root itself counts as allowed: Settings deletes a configured directory
 * wholesale ("delete all my models"), so refusing the root would break it.
 *
 * Containment is computed with `relative`, never a string prefix compare.
 * `startsWith` treats `<root>-evil` and `<root>XYZ` as inside `<root>`, which is
 * how a recursive delete came to accept sibling directories. Issue #4 named that
 * bypass; this is the predicate that ends it.
 */
export function isAtOrWithinRoot(rootDir: string, candidatePath: string): boolean {
  if (!rootDir || !candidatePath) return false

  const root = resolvePath(rootDir)
  const candidate = resolvePath(candidatePath)
  if (candidate === root) return true

  // On Windows a different drive yields an absolute relative-path, caught below.
  const rel = relative(root, candidate).replace(/\\/g, '/')
  return rel !== '' && rel !== '..' && !rel.startsWith('../') && !isAbsolute(rel)
}

// ─── Internal (non-extension) dir names inside extensionsDir ─────────────────
// Extension ids can never start with a dot, so dot-prefixed names are reserved
// for install machinery: staging copies, backups of the previous version.
// Both the Electron and Python discovery sides must skip them.

export const EXT_BACKUP_PREFIX  = '.modly-backup-'
export const EXT_STAGING_PREFIX = '.modly-staging-'
// Marker file inside an extension folder while its setup is still running —
// presence after a crash means the install never completed.
export const EXT_INCOMPLETE_MARKER = '.modly-incomplete'
// Reserved basename for registration-pending state. Active transactions append
// "-<extension-id>-<timestamp>" and live beside extension folders so linked
// source trees are never mutated.
export const EXT_REGISTRATION_PENDING_MARKER = '.modly-registration-pending'
// Reserved basename for the matching validated transaction commit marker.
export const EXT_VALIDATED_MARKER = '.modly-registration-validated'

export function isInternalExtensionDirName(name: string): boolean {
  return name.startsWith('.')
}

export function buildExtensionBackupPath(rootDir: string, extensionId: unknown, suffix: string): string {
  const safeId = assertSafeExtensionId(extensionId)
  return resolvePathWithinRoot(rootDir, `${EXT_BACKUP_PREFIX}${safeId}-${suffix}`)
}

// A backup dir is the previous version of an extension, parked during an
// install swap. Its name embeds the extension id: .modly-backup-<id>-<ts>.
// Ids may contain '-', so strip the numeric timestamp suffix, not a naive split.
export function parseExtensionBackupName(name: string): { extensionId: string } | null {
  if (!name.startsWith(EXT_BACKUP_PREFIX)) return null
  const rest  = name.slice(EXT_BACKUP_PREFIX.length)
  const match = rest.match(/^(.+)-\d+$/)
  if (!match) return null
  try {
    return { extensionId: assertSafeExtensionId(match[1]) }
  } catch {
    return null
  }
}

// Staging dir lives next to the final location so activation is a same-volume
// atomic rename. Unique per attempt (suffix) so a new install can never merge
// into the leftovers of a previous one, and never races the startup purge.
export function buildExtensionStagingPath(rootDir: string, extensionId: unknown, suffix: string): string {
  const safeId = assertSafeExtensionId(extensionId)
  return resolvePathWithinRoot(rootDir, `${EXT_STAGING_PREFIX}${safeId}-${suffix}`)
}


/**
 * Resolve caller-supplied name segments to a path inside `rootDir`.
 *
 * Segments are names ("Session", "cube.glb"), never paths. Joining them onto a
 * root without checking is how workspace:deleteCollection came to accept
 * "../../.." and recursively delete AppData.
 *
 * No segments means the root itself, which callers use to list it.
 */
export function resolveChildWithinRoot(rootDir: string, parts: string[]): string {
  if (parts.length === 0) return resolvePath(rootDir)
  return resolvePathWithinRoot(rootDir, join(...parts))
}
