import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const MAIN_DIR = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')

/** How a call to the LOCAL api is spelled, as opposed to github.com or HF. */
const LOCAL_API_TARGET = /API_BASE_URL|127\.0\.0\.1:8765|localhost:8765|PYTHON_API_URL/

function mainProcessSources() {
  return readdirSync(MAIN_DIR)
    .filter((name) => name.endsWith('.ts') && !name.endsWith('.test.ts'))
    .map((name) => ({ name, source: readFileSync(join(MAIN_DIR, name), 'utf8') }))
}

// The backend rejects any request without the token (issue #9), so a bare
// `axios.get(`${API_BASE_URL}/...`)` compiles fine and then 401s at runtime.
// Calls to the local API must go through apiClient, which attaches the header.
//
// Deliberately scoped to the local API: this process also talks to github.com
// and huggingface.co with bare axios, and those must NOT carry Modly's token.
test('no main-process code calls the local API with bare axios', () => {
  const offenders = []

  for (const { name, source } of mainProcessSources()) {
    if (name === 'api-client.ts') continue

    const lines = source.split('\n')
    lines.forEach((line, index) => {
      if (!/axios\s*\.\s*(get|post|put|patch|delete|request)\s*\(/.test(line)) return
      // The URL may sit on the following line in a wrapped call.
      const statement = line + '\n' + (lines[index + 1] ?? '')
      if (!LOCAL_API_TARGET.test(statement)) return
      // python-bridge polls /health, which is deliberately exempt from the token
      // check so a misconfigured backend fails as a diagnosable 401 on real
      // routes rather than as a startup that never reports ready.
      if (name === 'python-bridge.ts' && statement.includes('/health')) return
      offenders.push(`${name}:${index + 1}: ${line.trim()}`)
    })
  }

  assert.deepEqual(
    offenders,
    [],
    'These call the local API without the auth token and will 401 at runtime. '
    + 'Use apiClient from ./api-client instead:\n  ' + offenders.join('\n  '),
  )
})

// Asserted on the specific call site rather than swept for, because the other
// net.fetch in this process targets raw.githubusercontent.com and a sweep
// cannot tell the two apart from the call line alone.
test('the model download stream sends the auth header', () => {
  const source = readFileSync(join(MAIN_DIR, 'model-downloader.ts'), 'utf8')
  const fetchLine = source
    .split('\n')
    .find((line) => /net\.fetch\s*\(/.test(line))

  assert.ok(fetchLine, 'expected a net.fetch call in model-downloader.ts')
  assert.match(
    fetchLine,
    /apiAuthHeaders\(\)/,
    'the HF download stream hits the local API, so it must carry the token',
  )
})

test('the token is never attached to the global axios default', () => {
  // A default header on the global axios would send Modly's token to
  // github.com and huggingface.co along with everything else.
  const source = readFileSync(join(MAIN_DIR, 'api-client.ts'), 'utf8')
  assert.ok(
    !/axios\.defaults\.headers/.test(source),
    'api-client must configure its own instance, never axios.defaults',
  )
  assert.ok(
    /axios\.create\(/.test(source),
    'api-client should build a dedicated axios instance',
  )
})
