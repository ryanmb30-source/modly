import assert from 'node:assert/strict'
import test from 'node:test'

import { buildMeshExportUrl, toApiMeshPath } from './meshPath.ts'

test('a generated mesh URL becomes a workspace-relative path', () => {
  assert.equal(toApiMeshPath('/workspace/Session/cube.glb'), 'Session/cube.glb')
})

test('an imported mesh URL becomes the real path on disk (issue #10)', () => {
  // The case that was broken: stripping '/workspace/' is a no-op here, so the
  // whole URL used to be sent as `path` and the backend answered 400.
  const url = '/optimize/serve-file?path=C%3A%5CProjects%5Cassets%5Cadventurer.glb'
  assert.equal(toApiMeshPath(url), 'C:\\Projects\\assets\\adventurer.glb')
})

test('an imported path is never left URL-encoded', () => {
  const url = '/optimize/serve-file?path=C%3A%5CMy%20Meshes%5Ca%20cube.glb'
  const result = toApiMeshPath(url)
  assert.ok(!result.includes('%'), `still encoded: ${result}`)
  assert.equal(result, 'C:\\My Meshes\\a cube.glb')
})

test('a path containing its own "path=" survives intact', () => {
  // Guards the previous implementation's `url.split('path=')[1]`, which
  // truncated at the second occurrence.
  const url = '/optimize/serve-file?path=C%3A%5Cwork%5Cpath%3Dweird%5Cmesh.glb'
  assert.equal(toApiMeshPath(url), 'C:\\work\\path=weird\\mesh.glb')
})

test('anything else is passed through unchanged', () => {
  assert.equal(toApiMeshPath('Session/cube.glb'), 'Session/cube.glb')
  assert.equal(toApiMeshPath(''), '')
})

test('the result is never a URL, whichever route the mesh took', () => {
  // The single property every /optimize consumer depends on.
  const urls = [
    '/workspace/Session/cube.glb',
    '/optimize/serve-file?path=C%3A%5Cassets%5Ccube.glb',
  ]
  for (const url of urls) {
    const result = toApiMeshPath(url)
    assert.ok(
      !result.startsWith('/optimize/') && !result.startsWith('/workspace/'),
      `${url} produced something still URL-shaped: ${result}`,
    )
  }
})

test('exporting an imported mesh sends the disk path, not the serve-file URL (issue #10)', () => {
  // The live failure: `path` arrived as a URL and the backend answered
  // 400 Invalid path. Asserted on the built URL rather than on the helper,
  // because the helper was already correct -- the export call site was not.
  const url = buildMeshExportUrl(
    'http://127.0.0.1:8765',
    '/optimize/serve-file?path=C%3A%5CProjects%5Cassets%5Cadventurer.glb',
    'obj',
  )

  const path = new URL(url).searchParams.get('path')
  assert.equal(path, 'C:\\Projects\\assets\\adventurer.glb')
  assert.ok(
    !path?.includes('serve-file'),
    `a URL was passed where a path was expected: ${path}`,
  )
})

test('exporting a generated mesh sends the workspace-relative path', () => {
  const url = buildMeshExportUrl('http://127.0.0.1:8765', '/workspace/Session/cube.glb', 'stl')
  assert.equal(new URL(url).searchParams.get('path'), 'Session/cube.glb')
  assert.equal(new URL(url).searchParams.get('format'), 'stl')
})
