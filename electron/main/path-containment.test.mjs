import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const MAIN_DIR = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')

async function loadGuard() {
  return import(new URL('./extension-path-guard.ts', import.meta.url).href)
}

const ROOT = resolve('/tmp/modly-root')

test('a path inside the root is contained', async () => {
  const { isAtOrWithinRoot } = await loadGuard()
  assert.equal(isAtOrWithinRoot(ROOT, join(ROOT, 'Session')), true)
  assert.equal(isAtOrWithinRoot(ROOT, join(ROOT, 'a', 'b', 'c.glb')), true)
})

test('the root itself is allowed, because Settings deletes it wholesale', async () => {
  const { isAtOrWithinRoot } = await loadGuard()
  assert.equal(isAtOrWithinRoot(ROOT, ROOT), true)
})

test('a sibling sharing the name prefix is NOT contained', async () => {
  const { isAtOrWithinRoot } = await loadGuard()
  // The bypass. Both of these passed the old `resolved.startsWith(root)` check
  // and reached a recursive delete.
  assert.equal(isAtOrWithinRoot(ROOT, `${ROOT}-evil`), false)
  assert.equal(isAtOrWithinRoot(ROOT, `${ROOT}XYZ`), false)
  assert.equal(isAtOrWithinRoot(ROOT, `${ROOT}-evil/nested/deep`), false)
})

test('traversal out of the root is not contained', async () => {
  const { isAtOrWithinRoot } = await loadGuard()
  assert.equal(isAtOrWithinRoot(ROOT, join(ROOT, '..')), false)
  assert.equal(isAtOrWithinRoot(ROOT, join(ROOT, '..', '..', 'Windows')), false)
  assert.equal(isAtOrWithinRoot(ROOT, resolve('/etc/passwd')), false)
})

test('empty inputs are not contained', async () => {
  const { isAtOrWithinRoot } = await loadGuard()
  assert.equal(isAtOrWithinRoot('', ROOT), false)
  assert.equal(isAtOrWithinRoot(ROOT, ''), false)
})

test('workspace name segments cannot escape the workspace root', async () => {
  const { resolveChildWithinRoot } = await loadGuard()

  // Names, the legitimate shape.
  assert.equal(resolveChildWithinRoot(ROOT, ['Session']), join(ROOT, 'Session'))
  assert.equal(resolveChildWithinRoot(ROOT, ['Session', 'cube.glb']), join(ROOT, 'Session', 'cube.glb'))
  // No segments means the root, which listing uses.
  assert.equal(resolveChildWithinRoot(ROOT, []), ROOT)

  // The traversal that made workspace:deleteCollection an rm -rf on AppData.
  for (const parts of [['..'], ['..', '..'], ['..', '..', '..'], ['Session', '..', '..']]) {
    assert.throws(
      () => resolveChildWithinRoot(ROOT, parts),
      /escapes root/i,
      `${JSON.stringify(parts)} should not resolve outside the root`,
    )
  }
})

test('only web URLs may be handed to the OS', async () => {
  const { isAllowedExternalUrl } = await loadGuard()

  assert.equal(isAllowedExternalUrl('https://github.com/lightningpixel/modly'), true)
  assert.equal(isAllowedExternalUrl('http://localhost:8765/docs'), true)
  assert.equal(isAllowedExternalUrl('mailto:someone@example.com'), true)

  // shell.openExternal resolves these against OS protocol handlers.
  assert.equal(isAllowedExternalUrl('file:///C:/Windows/System32/calc.exe'), false)
  assert.equal(isAllowedExternalUrl('ms-msdt:/id PCWDiagnostic'), false)
  assert.equal(isAllowedExternalUrl('search-ms:query=passwords'), false)
  assert.equal(isAllowedExternalUrl('javascript:alert(1)'), false)
  assert.equal(isAllowedExternalUrl('\\\\attacker\\share\\payload.exe'), false)
  assert.equal(isAllowedExternalUrl(''), false)
  assert.equal(isAllowedExternalUrl(null), false)
  assert.equal(isAllowedExternalUrl(42), false)
})

// ─── The gate ────────────────────────────────────────────────────────────────
//
// Issue #4 named the string-prefix containment bypass and fixed two call sites.
// Three more were found in optimize.py months later, and a sixth in a recursive
// delete after that. Naming a defect class is not the same as sweeping it, so
// this fails the build when a new instance appears rather than relying on the
// next reader to remember.

const PATHY = /(dir|Dir|path|Path|root|Root|folder|Folder)/

function sourceFiles(dir, collected = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'out') continue
      sourceFiles(full, collected)
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      collected.push(full)
    }
  }
  return collected
}

// A wiring assertion, not a behavioural one: resolveChildWithinRoot is tested
// above, but it is reached through a closure inside setupIpcHandlers, so nothing
// else would notice workspacePath regressing to a bare join(). The handlers it
// feeds include a recursive delete, which is why this is worth pinning at all.
test('workspacePath routes through the containment helper', () => {
  const source = readFileSync(join(MAIN_DIR, 'ipc-handlers.ts'), 'utf8')
  const definition = source
    .split('\n')
    .findIndex((line) => /const workspacePath\s*=/.test(line))

  assert.ok(definition !== -1, 'workspacePath definition not found — has it been renamed?')

  const body = source.split('\n').slice(definition, definition + 4).join('\n')
  assert.match(
    body,
    /resolveChildWithinRoot/,
    'workspacePath must confine caller-supplied names to the workspace root; '
    + 'a bare join() lets "../../.." reach a recursive delete outside it',
  )
})

test('no main-process or renderer code decides path containment with a string prefix', () => {
  const offenders = []
  const RENDERER_DIR = resolve(MAIN_DIR, '..', '..', 'src')

  for (const file of [...sourceFiles(MAIN_DIR), ...sourceFiles(RENDERER_DIR)]) {
    const name = file.slice(resolve(MAIN_DIR, '..', '..').length + 1).replace(/\\/g, '/')
    const source = readFileSync(file, 'utf8')
    source.split('\n').forEach((line, index) => {
      // Comments describing the antipattern are not instances of it.
      const trimmed = line.trim()
      if (trimmed.startsWith('*') || trimmed.startsWith('//') || trimmed.startsWith('/*')) return

      const match = line.match(/(\w+)\s*\.startsWith\(\s*([^)]*)\)/)
      if (!match) return

      const [, receiver, argument] = match
      // `x.startsWith('..')` on the result of relative() is the CORRECT idiom;
      // the bug is comparing against another path value.
      if (/^['"`]/.test(argument.trim())) return
      if (!PATHY.test(receiver) && !PATHY.test(argument)) return

      offenders.push(`${name}:${index + 1}: ${line.trim()}`)
    })
  }

  assert.deepEqual(
    offenders,
    [],
    'String-prefix path containment is bypassable: "<root>-evil" tests as being '
    + 'inside "<root>". Use isAtOrWithinRoot / resolvePathWithinRoot from '
    + 'extension-path-guard instead:\n  ' + offenders.join('\n  '),
  )
})
