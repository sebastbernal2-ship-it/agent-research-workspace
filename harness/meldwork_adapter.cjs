'use strict'

const fs = require('node:fs')
const crypto = require('node:crypto')
const path = require('node:path')
const { parseV4CollaborationReceipt } = require(path.resolve(
  __dirname,
  '../spikes/meldwork-review/upstream/desktop/src/collaboration/collaboration-receipt.cjs',
))
const { v4ContextMethods } = require(path.resolve(
  __dirname,
  '../spikes/meldwork-review/upstream/desktop/src/workspace/local-workspace-auto-runner-v4-context.cjs',
))

const MESSAGE_TYPE_BY_PHASE = Object.freeze({
  proposal: 'proposal',
  discussion: 'revision',
  challenge: 'challenge',
  work: 'status',
  synthesis: 'decision',
  verification: 'evidence',
})

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function digest(value) {
  return crypto.createHash('sha256').update(value, 'utf8').digest('hex')
}

function sanitizeString(value) {
  return v4ContextMethods.v4SanitizeDeliveryText.call(v4ContextMethods, value, 10000)
}

function sanitizeReceipt(value) {
  if (typeof value === 'string') return sanitizeString(value)
  if (Array.isArray(value)) return value.map(sanitizeReceipt)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, sanitizeReceipt(child)]))
  }
  return value
}

function requireString(value, field, max = 2048) {
  if (typeof value !== 'string' || value.length < 1 || value.length > max) {
    throw new Error(`invalid ${field}`)
  }
  return value
}

function toCanonicalEvent(request) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('request must be an object')
  const projectId = requireString(request.projectId, 'projectId', 128)
  const workId = requireString(request.workId, 'workId', 128)
  const sourceReceiptId = requireString(request.sourceReceiptId, 'sourceReceiptId', 256)
  const occurredAt = requireString(request.occurredAt, 'occurredAt', 40)
  const actor = request.actor
  if (!actor || typeof actor !== 'object' || Array.isArray(actor)) throw new Error('invalid actor')
  if (!Array.isArray(request.visibleTo) || request.visibleTo.length > 256
      || request.visibleTo.some(value => typeof value !== 'string' || value.length > 128)) {
    throw new Error('invalid visibleTo')
  }

  const parsed = parseV4CollaborationReceipt(
    { text: requireString(request.text, 'text', 10000), collaboration: request.collaboration },
    true,
    requireString(request.phase, 'phase', 32),
  )
  const phase = parsed.collaboration.phase
  const safeText = sanitizeString(parsed.text)
  const safeReceipt = sanitizeReceipt(parsed.collaboration)
  if (!safeText || !safeReceipt.summary) throw new Error('sanitized V4 contribution is empty')
  const blobContent = stableJson({
    format: 'meldwork-v4-phase-v1',
    phase,
    visibleText: safeText,
    receipt: safeReceipt,
  })
  const contentHash = digest(blobContent)
  const identity = `${projectId}\0${workId}\0${sourceReceiptId}`
  const identityHash = digest(identity)
  const eventId = `event.meldwork.${identityHash}`
  const threadId = `thread.meldwork.${digest(`${projectId}\0${workId}`)}`
  const actorId = requireString(actor.actorId, 'actor.actorId', 128)
  const event = {
    recordType: 'event',
    schemaVersion: '0.1',
    eventId,
    projectId,
    eventType: 'discussion.message_added',
    actor,
    occurredAt,
    causationId: request.causationId || null,
    correlationId: threadId,
    idempotencyKey: eventId,
    payload: {
      workId,
      threadId,
      messageType: MESSAGE_TYPE_BY_PHASE[phase],
      contentRef: `blob.${contentHash}`,
      contentHash,
      visibleTo: [...new Set([...request.visibleTo, actorId])].sort(),
    },
  }
  return { event, blobContent }
}

function main() {
  const input = fs.readFileSync(0, 'utf8').trim()
  if (!input || Buffer.byteLength(input) > 65536) throw new Error('input must be 1..65536 bytes')
  const request = JSON.parse(input)
  process.stdout.write(`${JSON.stringify(toCanonicalEvent(request))}\n`)
}

if (require.main === module) {
  try {
    main()
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`)
    process.exitCode = 2
  }
}

module.exports = { toCanonicalEvent }
