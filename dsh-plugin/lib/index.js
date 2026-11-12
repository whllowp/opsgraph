/**
 * Host half of dsh-opsgraph-pet.
 *
 * It intentionally has no dependencies and no tools: the browser half talks to
 * the local OpsGraph HTTP API directly, so a linked plugin cannot break the
 * DSH plugin tree by resolving a missing package. The Loader entry exists so
 * the client module scanner sees `dsh.client`.
 */

export const name = 'opsgraph-pet'

export function apply() {
  // UI lives in ./client.js; the host half is a lifecycle anchor.
}
