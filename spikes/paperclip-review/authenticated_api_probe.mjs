#!/usr/bin/env node
// Synthetic loopback-only auth/tenant probe against a disposable Paperclip instance.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

const base = process.env.PAPERCLIP_BASE_URL ?? "http://127.0.0.1:3105";
const home = process.env.PAPERCLIP_HOME;
const configPath = process.env.PAPERCLIP_CONFIG;
const upstream = process.env.PAPERCLIP_UPSTREAM_DIR;
const origin = new URL(base);
if (origin.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname)) {
  throw new Error("Probe refuses non-loopback HTTP targets");
}
if (!home || !configPath || !upstream || !existsSync(configPath)) {
  throw new Error("Set PAPERCLIP_HOME, PAPERCLIP_CONFIG, and PAPERCLIP_UPSTREAM_DIR for a disposable instance");
}
const inviteScript = path.join(upstream, "packages/db/scripts/create-auth-bootstrap-invite.ts");
if (!existsSync(inviteScript)) throw new Error(`Missing Paperclip bootstrap helper: ${inviteScript}`);
const nonce = randomUUID();

async function signup(label) {
  const response = await fetch(new URL("/api/auth/sign-up/email", base), {
    method: "POST",
    headers: { Origin: origin.origin, "Content-Type": "application/json" },
    body: JSON.stringify({
      email: `${label.toLowerCase().replaceAll(" ", "-")}-${nonce}@paperclip.local`,
      password: "Synthetic-probe-password-123!",
      name: label,
    }),
  });
  const body = await response.json().catch(() => null);
  const cookies = response.headers.getSetCookie().map((value) => value.split(";", 1)[0]);
  assert.equal(response.status, 200, `signup ${label}: ${response.status} ${JSON.stringify(body)}`);
  assert(cookies.some((cookie) => cookie.toLowerCase().includes("session_token")), "signup did not set a session cookie");
  return { cookie: cookies.join("; "), user: body.user };
}

async function request(session, route, { method = "GET", body } = {}) {
  const response = await fetch(new URL(route, base), {
    method,
    headers: {
      ...(session ? { Cookie: session.cookie } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(method === "GET" || method === "HEAD" ? {} : { Origin: origin.origin }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const text = await response.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* retain text for assertion diagnostics */ }
  return { status: response.status, ok: response.ok, json, text };
}

const health = await request(null, "/api/health");
assert.equal(health.json?.deploymentMode, "authenticated", "target is not in authenticated mode");
const owner = await signup("Synthetic Owner");
const bootstrapUrl = execFileSync("pnpm", [
  "--filter", "@paperclipai/db", "exec", "tsx", inviteScript,
  "--config", configPath, "--base-url", base,
], {
  cwd: upstream,
  encoding: "utf8",
  env: { ...process.env, PAPERCLIP_HOME: home, NO_COLOR: "1", FORCE_COLOR: "0" },
}).trim();
const bootstrapToken = new URL(bootstrapUrl).pathname.split("/").at(-1);
const claim = await request(owner, `/api/invites/${bootstrapToken}/accept`, {
  method: "POST", body: { requestType: "human" },
});
assert.equal(claim.status, 202, `bootstrap claim: ${claim.status} ${claim.text}`);

const companyAResult = await request(owner, "/api/companies", {
  method: "POST", body: { name: `Synthetic A ${nonce}` },
});
assert.equal(companyAResult.status, 201, `create company A: ${companyAResult.status} ${companyAResult.text}`);
const companyBResult = await request(owner, "/api/companies", {
  method: "POST", body: { name: `Synthetic B ${nonce}` },
});
assert.equal(companyBResult.status, 201, `create company B: ${companyBResult.status} ${companyBResult.text}`);
const companyA = companyAResult.json;
const companyB = companyBResult.json;

const inviteResult = await request(owner, `/api/companies/${companyA.id}/invites`, {
  method: "POST", body: { allowedJoinTypes: "human", humanRole: "operator" },
});
assert.equal(inviteResult.status, 201, `create company invite: ${inviteResult.status} ${inviteResult.text}`);
const companyInviteToken = new URL(inviteResult.json.inviteUrl, base).pathname.split("/").at(-1);
const member = await signup("Synthetic Member");
const joined = await request(member, `/api/invites/${companyInviteToken}/accept`, {
  method: "POST", body: { requestType: "human" },
});
assert.equal(joined.status, 202, `company invite accept: ${joined.status} ${joined.text}`);

const members = await request(owner, `/api/companies/${companyA.id}/members`);
assert.equal(members.status, 200, `owner member list: ${members.status} ${members.text}`);
const memberRecord = members.json.members.find((record) => record.user?.email === member.user.email);
assert(memberRecord, "invited user is missing from company membership");
assert.equal(memberRecord.membershipRole, "operator");

const crossCompanyRead = await request(member, `/api/companies/${companyB.id}/members`);
assert([403, 404].includes(crossCompanyRead.status), `cross-company membership read returned ${crossCompanyRead.status}`);
const downgrade = await request(owner, `/api/companies/${companyA.id}/members/${memberRecord.id}`, {
  method: "PATCH", body: { membershipRole: "viewer" },
});
assert.equal(downgrade.status, 200, `viewer role update: ${downgrade.status} ${downgrade.text}`);
const viewerInvite = await request(member, `/api/companies/${companyA.id}/invites`, {
  method: "POST", body: { allowedJoinTypes: "human", humanRole: "viewer" },
});
assert.equal(viewerInvite.status, 403, `viewer invite creation returned ${viewerInvite.status}, expected 403`);

console.log(JSON.stringify({
  deploymentMode: health.json.deploymentMode,
  bootstrapClaimStatus: claim.status,
  companyInviteAcceptStatus: joined.status,
  joinedRole: memberRecord.membershipRole,
  crossCompanyMembersStatus: crossCompanyRead.status,
  viewerInviteCreationStatus: viewerInvite.status,
  canonicalProjectStore: "not integrated; this probes Paperclip authorization only",
}, null, 2));
