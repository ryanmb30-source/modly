/**
 * File persistence for extension install consent (issue #7).
 *
 * Kept apart from extension-trust.ts so the decision logic there stays pure and
 * testable without touching disk. This module is the only part that does IO.
 */
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'

import { parseTrustStore, type TrustStore } from './extension-trust'

function trustPath(userData: string): string {
  return join(userData, 'extension-trust.json')
}

/** Any failure reads as "nothing is trusted", which fails closed. */
export function loadTrustStore(userData: string): TrustStore {
  const file = trustPath(userData)
  if (!existsSync(file)) return {}
  try {
    return parseTrustStore(readFileSync(file, 'utf-8'))
  } catch {
    return {}
  }
}

export function saveTrustStore(userData: string, store: TrustStore): void {
  writeFileSync(trustPath(userData), JSON.stringify(store, null, 2), 'utf-8')
}
