'use strict'

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { LocalWorkspace } = require(path.resolve(
  __dirname,
  '../spikes/meldwork-review/upstream/desktop/src/workspace/local-workspace.cjs',
))
const { createCoordinationPlan } = require(path.resolve(
  __dirname,
  '../spikes/meldwork-review/upstream/desktop/src/collaboration/orchestration-v4-records.cjs',
))
const { fixture } = require(path.resolve(
  __dirname,
  '../spikes/meldwork-review/upstream/desktop/test/support/local-workspace-test-helpers.cjs',
))

const REPO_ROOT = path.resolve(__dirname, '..')
const PYTHON = path.join(REPO_ROOT, 'spikes/agenticx-runtime/.venv/bin/python')
const WORKER = path.join(__dirname, 'agenticx_worker.py')
let activeDirectory = ''

function callAgenticX(taskId, description, scriptedResult) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'meldwork-agenticx-worker-'))
  try {
    const result = spawnSync(PYTHON, [WORKER], {
      input: `${JSON.stringify({ taskId, description, scriptedResult })}\n`,
      encoding: 'utf8',
      cwd: home,
      env: {
        HOME: home,
        PATH: process.env.PATH || '/usr/bin:/bin',
        PYTHONDONTWRITEBYTECODE: '1',
        PYTHONNOUSERSITE: '1',
      },
      timeout: 20000,
      maxBuffer: 128 * 1024,
    })
    if (result.error) throw result.error
    if (result.status !== 0) throw new Error(`AgenticX worker failed (${result.status}): ${String(result.stderr).slice(-2000)}`)
    const lines = result.stdout.trim().split(/\r?\n/)
    if (lines.length !== 1) throw new Error(`expected one AgenticX JSON response, got ${lines.length}`)
    const response = JSON.parse(lines[0])
    if (response.success !== true || typeof response.result !== 'string') throw new Error('invalid AgenticX worker response')
    return response.result
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
}

function assignmentsFor(snapshotHash) {
  return [
    {
      taskId: 'gather-evidence', ownerKind: 'codex', role: 'worker',
      objective: 'Gather evidence about the competing explanations.',
      expectedOutput: 'A concise evidence-backed finding.',
      inputRefs: [], artifactIds: [], dependsOn: [],
    },
    {
      taskId: 'integrate-findings', ownerKind: 'hermes', role: 'integrator',
      objective: 'Integrate the evidence and state a corrected conclusion.',
      expectedOutput: 'A sourced conclusion with limitations.',
      inputRefs: [], artifactIds: [], dependsOn: ['gather-evidence'],
    },
  ]
}

function receiptFor(phase, agent, prompt, group, workspace, targetKinds) {
  const common = { version: 1, phase, summary: `${agent.kind} completed the ${phase} phase.` }
  if (phase === 'proposal') {
    return {
      ...common,
      capabilities: ['public-source analysis'],
      intendedWork: ['compare explanations against observations'],
      deliverables: [`${agent.kind} proposal`],
      dependencies: [],
    }
  }
  if (phase === 'challenge') {
    const snapshotHash = workspace.activeRuns.get(group.id).orchestration.snapshotHash
    const plan = createCoordinationPlan({
      snapshotHash,
      targetKinds,
      assignments: assignmentsFor(snapshotHash),
      finalizerKind: 'hermes',
      verifierKinds: ['codex'],
      agreedBy: targetKinds,
    })
    return {
      ...common,
      verdict: 'support',
      proposedAssignments: plan.assignments,
      finalizerKind: 'hermes',
      verifierKinds: ['codex'],
      agreeToPlan: true,
    }
  }
  if (phase === 'work') {
    const workItemId = prompt.match(/^Work item: ([A-Za-z0-9._:-]+)$/m)?.[1]
    if (!workItemId) throw new Error('Meldwork work prompt has no Work item ID')
    return { ...common, workItemId, deliverables: [`${agent.kind} delivered ${workItemId}`] }
  }
  if (phase === 'synthesis') {
    return { ...common, resolvedIssueIds: [] }
  }
  if (phase === 'verification') {
    return { ...common, verdict: 'support' }
  }
  throw new Error(`unexpected Meldwork V4 phase: ${phase}`)
}

async function run() {
  const { directory, options } = fixture()
  activeDirectory = directory
  const targetKinds = ['codex', 'hermes']
  const invocations = []
  let workspace
  let group
  options.defaultYolo = false
  options.runAgent = async (agent, prompt, _workdir, runOptions) => {
    const phase = prompt.match(/^Phase: ([a-z-]+)$/m)?.[1]
    if (!phase) throw new Error('Meldwork V4 prompt omitted its phase')
    const taskId = `task.${agent.kind}.${phase}.${invocations.length + 1}`
    const resultText = callAgenticX(
      taskId,
      `Execute Meldwork V4 ${phase} as an AgenticX task.\n${prompt.slice(0, 4000)}`,
      `${agent.kind} ${phase}: deterministic AgenticX AgentExecutor result.`,
    )
    const collaboration = receiptFor(phase, agent, prompt, group, workspace, targetKinds)
    const invocation = {
      sourceReceiptId: `run.${runOptions.operationId || invocations.length}.${agent.kind}.${phase}`,
      phase,
      agentKind: agent.kind,
      text: resultText,
      collaboration,
      sessionRef: runOptions.sessionRef || `${agent.kind}-session`,
      operationId: runOptions.operationId,
      occurredAt: new Date().toISOString(),
    }
    invocations.push(invocation)
    return invocation
  }

  workspace = new LocalWorkspace(options)
  await workspace.refreshAgents()
  group = workspace.createGroup({
    name: 'Canonical harness component integration',
    agentKinds: targetKinds,
    workdir: directory,
    allowWrite: false,
  })
  workspace.addMessage(group.id, 'user', 'Investigate a synthetic public-source claim; challenge it and revise from evidence.')
  await workspace.sendMessage({
    groupId: group.id,
    text: 'Investigate whether seasons are caused by Earth-Sun distance; challenge and verify the explanation.',
    mode: 'auto',
    maxRounds: 2,
    targetKinds,
    protocol: 'v4',
  })
  const controller = workspace.activeRuns.get(group.id)
  await controller.promise
  const snapshot = workspace.snapshot()
  const output = {
    orchestrationPhase: controller.orchestration.phase,
    stopReason: controller.stopReason || null,
    invocationCount: invocations.length,
    invocations,
    messages: snapshot.messages
      .filter(message => message.role === 'agent')
      .map(message => ({ agentKind: message.agentKind, content: message.content })),
    runId: controller.runId,
    taskId: controller.taskId,
  }
  if (output.orchestrationPhase !== 'completed') {
    throw new Error(`Meldwork V4 did not complete: ${JSON.stringify({ phase: output.orchestrationPhase, stopReason: output.stopReason, messages: snapshot.messages.slice(-8).map(({ role, content }) => ({ role, content })), slots: controller.orchestration.slots, calls: invocations.map(({ agentKind, phase }) => ({ agentKind, phase })), runError: controller.lastError || null }, null, 2)}`)
  }
  process.stdout.write(`${JSON.stringify(output)}\n`)
  fs.rmSync(directory, { recursive: true, force: true })
  activeDirectory = ''
}

run().catch(error => {
  if (activeDirectory) fs.rmSync(activeDirectory, { recursive: true, force: true })
  process.stderr.write(`${error.stack || error.message || String(error)}\n`)
  process.exitCode = 1
})
