---
title: Lokra Cloud
description: What Lokra Cloud does, how a gate connects to it, and exactly what data it receives and sends back.
---

[Lokra Cloud](https://cloud.lokra.dev) is a hosted inbox for every Lokra gate you run. From a laptop or a phone you can approve or deny the writes your agents propose, pause and resume agents, read the activity of every gate, and get an alert when a write needs you. The gate stays open source and keeps running in your network. Cloud only sees what the gate chooses to send it, and it never sees query results.

## What you can do in Cloud

Cloud has six pages. On a wide screen they sit in the left rail; on a phone, Inbox, Agents, Activity and Gates are in the tab bar and the rest are under **More**. Press `1` to `6` to switch pages, or `?` to see every shortcut.

| Page | What it shows |
| --- | --- |
| Inbox | Every write waiting for a decision, from every gate. **Hold to approve**, **Deny**, or pause the agent. Recently decided writes are listed under **Decided today**. |
| Agents | Every agent each gate serves, its last 60 minutes of activity, and whether it is paused. **Pause** and **Hold to resume**. |
| Activity | The signed ledger from every gate, with filters, a chain check and CSV export. See [Activity and export](/docs/cloud-activity). |
| Gates | Each connected gate, whether it is live, its Lokra version and agent count. Owners create gate tokens here. See [Connect a gate](/docs/cloud-gates). |
| Team | Who is in your organisation and their role. Owners invite people here. See [Team and roles](/docs/cloud-team). |
| Billing | Your plan, this month's usage and the plans on offer. See [Plans and billing](/docs/cloud-billing). |

Each request in the Inbox shows the statement and tables, the gate, the agent and who it acts for, the agent's reason, the masked SQL, how many rows the dry run says would change, any warnings, and whether it needs two people. On the keyboard, `J` and `K` move between requests, hold `A` to approve, `D` denies and `P` pauses the agent.

Phone alerts, hold to approve and two-person approvals are covered in [Approve from your phone](/docs/cloud-alerts).

## How a gate connects

A gate connects by running `lokra cloud` next to its `policies.yaml`, with a gate token from Cloud:

```bash
LOKRA_CLOUD_TOKEN=lkg_… lokra cloud
```

About every 3 seconds, `lokra cloud` sends one HTTPS request out to `https://cloud.lokra.dev/v1/sync`, with the gate token as a bearer token. The response carries any decisions people have made in Cloud. Nothing connects in to the gate, so it needs no public address, no open port and no VPN. It only needs to reach `cloud.lokra.dev` on port 443.

`lokra cloud` reads and writes the same state as the gate: the pending writes, pauses and ledger in the `.lokra` folder next to `policies.yaml`. That is why it has to run where the gate runs. [Connect a gate](/docs/cloud-gates) has the details.

If Cloud cannot be reached, `lokra cloud` keeps retrying, waiting longer each time up to 60 seconds. The gate itself does not depend on Cloud. Agents keep working, and you can still approve in the terminal, the local dashboard or [Slack](/docs/slack).

## What Cloud receives

Each sync sends the following, and nothing else.

| What | Fields |
| --- | --- |
| The gate | The Lokra version it runs. |
| Agents | Each agent in the policy: name, description, rate limit, and, if it is paused, who paused it, when and why. |
| Writes | Every pending write, and every write proposed, decided or run in the last 24 hours, up to 200: ID, status, agent, who it acts for, reason, statement, tables, masked SQL, warnings, rows the dry run would change, rows changed, who decided it, approvals so far, approvals needed, any failure, and when it was proposed. |
| Ledger entries | New entries since the last sync, up to 500 at a time, with every field except the signature: sequence number, time, event, agent, database role, who it acts for, purpose, token ID, tool, tables, masked SQL, row counts, how many values were masked, reason or database error, write ID, who decided, and the entry's hash and the previous entry's hash. |
| Results | For each decision the gate applied: whether it worked, and a short message. |

The SQL in writes and in ledger entries is masked on the gate before it is sent, the same way Lokra masks it in the ledger. Cloud shows masked values as labels such as `EMAIL` or `CARD`.

> **Note:** SQL text is masked by Lokra's detectors, which recognise values such as email addresses and card numbers. A value no detector recognises, such as a name in a `WHERE` clause, is sent as written. See [Masking](/docs/masking) to choose which detectors run.

When a gate connects for the first time, it also sends the entries already in its ledger, oldest first, 500 per sync, until Cloud has caught up.

## What Cloud never receives

- **Query results.** The rows an agent reads never leave your network. Ledger entries carry row counts, not rows.
- **Credentials.** Database connection strings, including the one you pass to `--admin-dsn`, agent database passwords, agent tokens and the ledger signing key all stay on the gate. Ledger entries carry a token's ID, never the token.
- **Signatures.** Cloud gets each entry's hash and the previous entry's hash, but not its signature.
- **The rest of your policy.** Table rules, masking rules, row locks and approval rules are not sent. Cloud sees only the agent fields listed above.

## How decisions come back

1. Someone with the owner or approver role approves, denies, pauses or resumes in Cloud.
2. Cloud checks their role and, for a write, that it is still pending. Then it queues a command for that gate.
3. On its next sync, a few seconds later, the gate picks the command up. Cloud shows **Gate received**.
4. The gate applies it with the same code as `lokra approve`, `lokra deny`, `lokra pause` and `lokra resume`, and records it in its own signed ledger. The decision is recorded under the person's GitHub username followed by `(cloud)`, such as `dana (cloud)`.
5. The gate reports the result at once, and Cloud shows the outcome, or **Failed** with the gate's reason.

There are only four commands: approve, deny, pause and resume. Cloud cannot run SQL, read data, change the policy or mint tokens. If the gate is offline, the command waits in Cloud and the gate applies it when it reconnects.

Approving a write does not run it. As with any approval, the agent then runs the approved write through Lokra, within the policy's `approval_ttl_seconds` (one hour by default). See [Approvals](/docs/approvals).

## Security

- **Sign-in.** You sign in with GitHub. Cloud asks GitHub only to read your profile (the `read:user` scope), and keeps your username, name and avatar. Your session is a signed cookie that scripts cannot read, sent only over HTTPS, and it lasts 30 days.
- **Gate tokens.** A gate token starts with `lkg_` and holds 24 random bytes. Cloud stores only its SHA-256 hash, so it shows you the token once. Removing the gate revokes its token.
- **Roles.** Cloud checks your role on the server for every decision, gate change, invite and billing action. See [Team and roles](/docs/cloud-team).
- **Phone alerts.** Alerts are encrypted end to end for your device (RFC 8291), so the push service that delivers them cannot read them. They name the agent, statement, table, reason, gate and row count, never the SQL. Cloud accepts subscriptions only for the browsers' own push services.
- **Page protections.** Cloud's pages load scripts, styles, fonts and data only from `cloud.lokra.dev`, and images only from there and from GitHub avatars. They cannot be framed by another site. Requests that change anything must carry a header other sites cannot add.
- **The ledger stays yours.** Each gate keeps its complete signed ledger. Cloud's copy is for reading and searching. To check signatures, run `lokra verify-ledger` on the gate. See [Audit ledger](/docs/audit).

## How to start

1. Open [cloud.lokra.dev](https://cloud.lokra.dev) and choose **Continue with GitHub**. The first time you sign in, unless someone has invited you, Cloud creates an organisation named after your GitHub username, with you as its owner.
2. Open **Gates**, name the gate and choose **Create token**.
3. Run the command Cloud shows next to the gate's `policies.yaml`.

[Connect a gate](/docs/cloud-gates) walks through each step and how to keep `lokra cloud` running. After that, [turn on phone alerts](/docs/cloud-alerts) and [invite your team](/docs/cloud-team). The Free plan needs no card; see [Plans and billing](/docs/cloud-billing).
