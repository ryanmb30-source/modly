/**
 * Per-extension consent for install-time code execution (issue #7).
 *
 * Installing an extension executes its code in several ways: npm lifecycle
 * scripts, `pip install` (sdists and PEP 517 backends run code), `setup.py`,
 * and importing `generator.py`. Nothing here can make that safe -- the point is
 * to make the moment of trust explicit, and to scope it to the exact version
 * the user agreed to.
 *
 * Two rules the design turns on:
 *
 * 1. Trust never derives from extension-supplied data. A manifest field saying
 *    "I need lifecycle scripts" is written by the same party the gate exists to
 *    contain, so it cannot be an input. Consent comes from the user only.
 *
 * 2. Consent is pinned to a commit, not to an extension id. `tarball/HEAD` is a
 *    moving ref: trusting `foo` once would otherwise trust every future version
 *    of `foo`, including one pushed after the fact.
 *
 * The functions here are pure so they can be tested without Electron; the
 * caller owns the dialog and the file IO.
 */

/** How an extension gets to run code during install. */
export type ExecutionPath = 'npm-scripts' | 'pip-install' | 'setup-py'

export interface ExtensionContents {
  hasPackageJson: boolean
  hasSetupPy: boolean
  hasRequirements: boolean
}

export interface TrustRecord {
  commitSha: string
  grantedAt: string
}

export type TrustStore = Record<string, TrustRecord>

const FULL_SHA = /^[0-9a-f]{40}$/i

/**
 * Pull the commit sha out of GitHub's tarball wrapper directory.
 *
 * A tarball from `/tarball/HEAD` wraps everything in `{owner}-{repo}-{sha}`,
 * where sha is the 7+ char abbreviated commit. The install extracts with
 * `strip: 1`, so this is read before that name is discarded.
 *
 * The repo name may itself contain dashes, so the sha is taken from the last
 * segment rather than by splitting into three.
 */
export function parseTarballCommitSha(topLevelDir: string): string | null {
  const trimmed = (topLevelDir || '').replace(/[/\\]+$/, '')
  const lastDash = trimmed.lastIndexOf('-')
  if (lastDash === -1) return null

  const candidate = trimmed.slice(lastDash + 1)
  if (!/^[0-9a-f]{7,40}$/i.test(candidate)) return null
  return candidate.toLowerCase()
}

export function normalizeCommitSha(sha: unknown): string | null {
  if (typeof sha !== 'string') return null
  const trimmed = sha.trim().toLowerCase()
  if (!FULL_SHA.test(trimmed) && !/^[0-9a-f]{7,40}$/.test(trimmed)) return null
  return trimmed
}

/**
 * Which execution paths this extension will actually use.
 *
 * Only paths present in the downloaded tree are listed, so the consent prompt
 * names what will really happen rather than a generic warning.
 */
export function detectExecutionPaths(contents: ExtensionContents): ExecutionPath[] {
  const paths: ExecutionPath[] = []
  if (contents.hasPackageJson) paths.push('npm-scripts')
  if (contents.hasRequirements) paths.push('pip-install')
  if (contents.hasSetupPy) paths.push('setup-py')
  return paths
}

const PATH_DESCRIPTIONS: Record<ExecutionPath, string> = {
  'npm-scripts': 'npm install may run this package\'s install scripts',
  'pip-install': 'pip install may build packages from source, which runs their setup code',
  'setup-py': 'setup.py will be executed to build the extension\'s environment',
}

export function describeExecutionPaths(paths: ExecutionPath[]): string[] {
  return paths.map((path) => PATH_DESCRIPTIONS[path])
}

export function trustKey(extensionId: string, commitSha: string): string {
  return `${extensionId}@${commitSha.toLowerCase()}`
}

/**
 * True only when this exact extension *and* commit was previously approved.
 *
 * A stored grant for a different sha is deliberately not a match: an update is
 * a fresh decision.
 */
export function isTrusted(store: TrustStore, extensionId: string, commitSha: string): boolean {
  const sha = normalizeCommitSha(commitSha)
  if (!sha) return false
  const record = store[trustKey(extensionId, sha)]
  return record?.commitSha === sha
}

export function recordTrust(
  store: TrustStore,
  extensionId: string,
  commitSha: string,
  grantedAt: string,
): TrustStore {
  const sha = normalizeCommitSha(commitSha)
  if (!sha) throw new Error(`Refusing to record trust for an unusable commit sha: ${commitSha}`)
  return { ...store, [trustKey(extensionId, sha)]: { commitSha: sha, grantedAt } }
}

/** Drops every grant for an extension -- used when it is uninstalled. */
export function revokeTrust(store: TrustStore, extensionId: string): TrustStore {
  const prefix = `${extensionId}@`
  return Object.fromEntries(
    Object.entries(store).filter(([key]) => !key.startsWith(prefix)),
  )
}

export function parseTrustStore(raw: string): TrustStore {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return {}
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}

  const store: TrustStore = {}
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!value || typeof value !== 'object') continue
    const sha = normalizeCommitSha((value as Record<string, unknown>).commitSha)
    const grantedAt = (value as Record<string, unknown>).grantedAt
    if (!sha || typeof grantedAt !== 'string') continue
    store[key] = { commitSha: sha, grantedAt }
  }
  return store
}

/**
 * npm arguments for an install.
 *
 * Denied: `--ignore-scripts`, a hard block.
 *
 * Granted: the flag is omitted entirely, so npm falls back to the user's own
 * npmrc. Consent unlocks "respect my configuration"; it never forces scripts
 * on. Passing `--ignore-scripts=false` here would override a user who set
 * `ignore-scripts=true` themselves, which is exactly the upstream behaviour
 * this fork reverted in 3587c37.
 */
export function npmInstallArgs(trusted: boolean): string[] {
  const base = ['install', '--omit=dev', '--no-audit', '--no-fund']
  return trusted ? base : [...base, '--ignore-scripts']
}
