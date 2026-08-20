import assert from 'node:assert/strict'
import test from 'node:test'

async function loadTrust() {
  return import(new URL('./extension-trust.ts', import.meta.url).href)
}

const SHA = 'a'.repeat(40)
const OTHER_SHA = 'b'.repeat(40)

test('parseTarballCommitSha reads the sha from GitHub\'s wrapper directory', async () => {
  const { parseTarballCommitSha } = await loadTrust()
  assert.equal(parseTarballCommitSha('lightningpixel-modly-1a2b3c4'), '1a2b3c4')
  // Repo names contain dashes, so the sha is the last segment, not the third.
  assert.equal(parseTarballCommitSha('owner-my-cool-repo-deadbee'), 'deadbee')
  assert.equal(parseTarballCommitSha('owner-repo-1a2b3c4/'), '1a2b3c4')
})

test('parseTarballCommitSha rejects names with no usable sha', async () => {
  const { parseTarballCommitSha } = await loadTrust()
  assert.equal(parseTarballCommitSha('norepo'), null)
  assert.equal(parseTarballCommitSha('owner-repo-nothexchars'), null)
  assert.equal(parseTarballCommitSha('owner-repo-abc'), null, 'too short to be a sha')
  assert.equal(parseTarballCommitSha(''), null)
})

test('detectExecutionPaths names only the paths the extension actually has', async () => {
  const { detectExecutionPaths } = await loadTrust()
  assert.deepEqual(
    detectExecutionPaths({ hasPackageJson: false, hasSetupPy: false, hasRequirements: false }),
    [],
  )
  assert.deepEqual(
    detectExecutionPaths({ hasPackageJson: true, hasSetupPy: false, hasRequirements: false }),
    ['npm-scripts'],
  )
  assert.deepEqual(
    detectExecutionPaths({ hasPackageJson: false, hasSetupPy: true, hasRequirements: true }),
    ['pip-install', 'setup-py'],
  )
})

test('describeExecutionPaths gives the prompt a line per real path', async () => {
  const { describeExecutionPaths } = await loadTrust()
  const lines = describeExecutionPaths(['npm-scripts', 'setup-py'])
  assert.equal(lines.length, 2)
  assert.match(lines[0], /npm install/)
  assert.match(lines[1], /setup\.py/)
})

test('an extension is trusted only at the exact commit that was approved', async () => {
  const { recordTrust, isTrusted } = await loadTrust()
  const store = recordTrust({}, 'mesh-process', SHA, '2026-08-20T12:00:00Z')

  assert.equal(isTrusted(store, 'mesh-process', SHA), true)
  assert.equal(
    isTrusted(store, 'mesh-process', OTHER_SHA),
    false,
    'a grant must not carry across to a version the user never saw',
  )
  assert.equal(isTrusted(store, 'other-extension', SHA), false)
})

test('trust lookups are case-insensitive about the sha', async () => {
  const { recordTrust, isTrusted } = await loadTrust()
  const store = recordTrust({}, 'mesh-process', SHA.toUpperCase(), '2026-08-20T12:00:00Z')
  assert.equal(isTrusted(store, 'mesh-process', SHA), true)
})

test('recordTrust refuses a sha it cannot verify', async () => {
  const { recordTrust } = await loadTrust()
  // Storing a grant under a junk key would make isTrusted unreachable for the
  // real sha, silently re-prompting forever -- or worse, matching junk.
  assert.throws(() => recordTrust({}, 'mesh-process', 'not-a-sha', 'now'), /commit sha/i)
  assert.throws(() => recordTrust({}, 'mesh-process', '', 'now'), /commit sha/i)
})

test('isTrusted rejects an unusable sha rather than matching loosely', async () => {
  const { isTrusted } = await loadTrust()
  const store = { 'mesh-process@': { commitSha: '', grantedAt: 'now' } }
  assert.equal(isTrusted(store as never, 'mesh-process', ''), false)
})

test('revokeTrust drops every grant for one extension and leaves the rest', async () => {
  const { recordTrust, revokeTrust, isTrusted } = await loadTrust()
  let store = recordTrust({}, 'mesh-process', SHA, 'now')
  store = recordTrust(store, 'mesh-process', OTHER_SHA, 'now')
  store = recordTrust(store, 'keep-me', SHA, 'now')

  const pruned = revokeTrust(store, 'mesh-process')
  assert.equal(isTrusted(pruned, 'mesh-process', SHA), false)
  assert.equal(isTrusted(pruned, 'mesh-process', OTHER_SHA), false)
  assert.equal(isTrusted(pruned, 'keep-me', SHA), true)
})

test('parseTrustStore survives a corrupt or hostile file without granting trust', async () => {
  const { parseTrustStore, isTrusted } = await loadTrust()
  assert.deepEqual(parseTrustStore('not json'), {})
  assert.deepEqual(parseTrustStore('[]'), {})
  assert.deepEqual(parseTrustStore('null'), {})

  // Entries missing a usable sha are dropped rather than trusted.
  const partial = parseTrustStore(JSON.stringify({
    [`good@${SHA}`]: { commitSha: SHA, grantedAt: 'now' },
    'bad@x': { commitSha: 'nope', grantedAt: 'now' },
    'alsobad@y': { commitSha: SHA },
  }))
  assert.equal(isTrusted(partial, 'good', SHA), true)
  assert.equal(Object.keys(partial).length, 1)
})

test('a round trip through the stored file preserves the grant', async () => {
  const { recordTrust, parseTrustStore, isTrusted } = await loadTrust()
  const store = recordTrust({}, 'mesh-process', SHA, '2026-08-20T12:00:00Z')
  const reloaded = parseTrustStore(JSON.stringify(store))
  assert.equal(isTrusted(reloaded, 'mesh-process', SHA), true)
})

test('npm install blocks lifecycle scripts unless the user approved this version', async () => {
  const { npmInstallArgs } = await loadTrust()
  assert.ok(
    npmInstallArgs(false).includes('--ignore-scripts'),
    'an unapproved extension must not run lifecycle scripts',
  )
})

test('approval omits the flag entirely rather than forcing scripts on', async () => {
  const { npmInstallArgs } = await loadTrust()
  const args = npmInstallArgs(true)
  assert.ok(!args.includes('--ignore-scripts'), 'approval must lift the block')
  assert.ok(
    !args.includes('--ignore-scripts=false'),
    'approval must not override a user who set ignore-scripts in their own npmrc -- ' +
    'that is the upstream behaviour reverted in 3587c37',
  )
  assert.deepEqual(args, ['install', '--omit=dev', '--no-audit', '--no-fund'])
})
