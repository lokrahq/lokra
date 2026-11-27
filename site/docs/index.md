---
title: Introduction
description: What Lokra is, how a request flows through it, and where to start.
---
Lokra sits between AI agents and your Postgres database. Each agent gets its own database role with only the tables, columns and rows its policy allows. Sensitive values are masked in what it reads, every write waits for approval, and every request lands in a signed audit ledger.

Most teams give an agent the same database password their app uses. That means one bad plan, or one instruction hidden in a customer's support ticket, can read or change anything. Lokra replaces that password with a scoped identity that the database itself enforces.

<div class="docs-cards">
<a href="/docs/quickstart"><b>Quickstart</b><span>Install Lokra, watch the demo and connect your first agent in about ten minutes.</span></a>
<a href="/docs/policies"><b>Write a policy</b><span>Decide what each agent may read, change and see unmasked.</span></a>
<a href="/docs/connect-clients"><b>Connect AI clients</b><span>Claude Code, Claude Desktop, Cursor, VS Code, Windsurf and any MCP client.</span></a>
<a href="/docs/cloud"><b>Lokra Cloud</b><span>One inbox to approve writes and pause agents from your phone.</span></a>
</div>

## How a request flows

An agent talks to Lokra through MCP tools such as `query` and `propose_write`. Every call goes through the same steps, and each layer still holds if the one before it fails:

1. **Identity.** The call carries a short-lived token signed by Lokra. Unknown, forged, expired, revoked or paused agents are refused.
2. **SQL check.** Lokra parses the statement. Reads must be a single `SELECT`; writes go to approval; DDL, stacked statements, `SET ROLE`, file and network functions are refused.
3. **The agent's own role.** The statement runs as that agent's Postgres role, which only has grants on the tables and columns in its policy. Row locks are row-level security policies on that role, so rows outside its scope do not exist as far as it can tell.
4. **Read-only transaction.** Reads run in a read-only transaction as prepared statements, with a statement timeout on the role.
5. **Masking.** Results are masked by column rules and value detectors, including inside free text, JSON and arrays.
6. **Ledger.** The request, its outcome and the masked SQL are appended to a hash-chained, signed ledger.

Writes take a longer path. Lokra dry-runs the statement to count the rows it would change, then holds it until a person approves, or until an [approval rule](/docs/approvals#approval-rules) lets a small one through. When the agent runs it, Lokra rolls it back if it would now change more rows than were approved.

The [security model](/docs/security-model) explains each layer in detail.

## The two parts

| | Lokra (open source) | Lokra Cloud |
|---|---|---|
| What it is | The gate: an MCP server that runs next to your database | A hosted inbox for every gate you run |
| Where it runs | Your laptop, server or container | cloud.lokra.dev |
| Licence and price | Apache-2.0, free, no limits | Free plan, then a flat $49 a month for the whole team |
| Sees your data | Yes, it is the path to your database | Never query results. Only masked SQL, row counts and the ledger |

The gate works on its own. You approve writes in the terminal, a local dashboard or Slack. [Lokra Cloud](/docs/cloud) adds one inbox across gates, phone alerts and team roles.

## What you need

- Postgres. Lokra is tested on Postgres 16 and uses standard features (roles, grants, row-level security), so hosted Postgres such as Supabase and Amazon RDS works too. See [connect your database](/docs/connect-database).
- Python 3.11 or newer, or Docker.
- An admin login that can create roles when you provision. It does not need to be a superuser.

Start with the [quickstart](/docs/quickstart).
