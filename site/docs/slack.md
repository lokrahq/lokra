---
title: Approve in Slack
description: Post pending writes to a Slack channel with Approve, Deny and Pause buttons, and record every decision in the signed ledger.
---
`lokra slack` posts each write that is waiting for approval to a Slack channel, with the agent, the person it acts for, its reason, the masked SQL and the dry-run row count, plus **Approve** and **Deny** buttons. Only the people you list can decide, and every decision lands in the [audit ledger](/docs/audit). It connects out to Slack over Socket Mode, so Lokra needs no public URL and no inbound firewall rule.

It works alongside the other ways to decide. A write approved or denied in the terminal, the dashboard or Lokra Cloud closes its Slack message too. For how approval rules decide which writes need a person at all, see [approvals](/docs/approvals).

## Before you start

You need:

- A working Lokra with at least one agent that can write. See [quickstart](/docs/quickstart).
- A Slack workspace where you can create apps.
- The machine that runs `lokra slack` must share Lokra's state directory (`.lokra`) with `lokra serve`, because pending writes and approvals live there. See [where state lives](/docs/production#where-state-lives).

## Set up

### 1. Create the Slack app

At [api.slack.com/apps](https://api.slack.com/apps), choose to create an app from a manifest, pick your workspace, and paste this YAML:

```yaml
display_information: {name: Lokra}
features: {bot_user: {display_name: Lokra}}
oauth_config: {scopes: {bot: [chat:write]}}
settings: {interactivity: {is_enabled: true}, socket_mode_enabled: true}
```

The app only needs `chat:write`: it posts and updates messages, and sends a private note to someone who presses a button they may not use. Socket Mode and interactivity let the buttons reach Lokra over its outbound connection.

Install the app to your workspace, then copy the **Bot User OAuth Token** from **OAuth & Permissions**. It starts with `xoxb-`.

### 2. Create an app-level token

Under **Basic Information**, in **App-Level Tokens**, generate a token with the `connections:write` scope. It starts with `xapp-`. This is what lets `lokra slack` open its Socket Mode connection.

### 3. Invite the bot to your channel

Create a channel for approvals, such as `#db-approvals`, and invite the Lokra bot to it, for example with `/invite @Lokra`. The bot can only post in channels it is a member of.

### 4. Add the channel and approvers to the policy

Add a `slack` section to `policies.yaml` with the channel ID and the Slack member IDs of the people who may decide:

```yaml
slack: {channel: C0123ABCD, approvers: [U0123ABCD, U0456EFGH]}
```

Use IDs, not names. In Slack, the channel ID is at the bottom of the channel's details, and a person's member ID is under **Copy member ID** in their profile menu. Saving the policy from the dashboard keeps this section.

### 5. Install the Slack extra

Slack support is an optional extra:

```bash
pipx install 'lokra[slack]'
```

If Lokra is already installed with pipx, run `pipx install --force 'lokra[slack]'` to add the extra. The [container image](/docs/production#run-in-a-container) already includes it.

### 6. Run it

Run `lokra slack` next to your policy, with both tokens in the environment:

```bash
LOKRA_SLACK_BOT_TOKEN=xoxb-... LOKRA_SLACK_APP_TOKEN=xapp-... \
  lokra slack --admin-dsn "postgresql://admin:secret@db.internal:5432/app"
```

```text
lokra: posting write approvals to Slack channel C0123ABCD (Ctrl+C to stop)
```

| Setting | What it does |
|---|---|
| `LOKRA_SLACK_BOT_TOKEN` | The bot token (`xoxb-...`). Required. |
| `LOKRA_SLACK_APP_TOKEN` | The app-level token (`xapp-...`). Required. |
| `--admin-dsn` | Admin connection string, so pausing from Slack also blocks the agent's database login. Default: `$LOKRA_ADMIN_DSN`. |

Keep it running for as long as you want approvals in Slack. It checks for new and decided writes every two seconds, and remembers which messages it posted in `.lokra/slack.json`, so a restart does not post them again. To run it as a service, follow [run in production](/docs/production#run-with-systemd) with `slack` in place of `cloud`.

## What the messages show

When an agent proposes a write that needs a person, Lokra posts a message like this:

```text
ops-bot wants to DELETE orders
w_28cbd037 · on behalf of dana

> remove cancelled orders for account 42
DELETE FROM orders WHERE status = 'cancelled' AND notes = '[EMAIL]'
Dry run: 3 rows would change
Needs two people

[Approve]  [Deny]  [Pause ops-bot]
```

| Part | Where it comes from |
|---|---|
| Agent, statement and tables | The proposed write |
| Write id and "on behalf of" | The write id the agent sees, and the person on the agent's token |
| Quoted line | The reason the agent gave, or "No reason given." |
| SQL | The statement, with values the policy's [detectors](/docs/masking) recognise masked. Very long SQL is cut short. |
| Dry run | How many rows the write changed when Lokra ran it and rolled it back |
| Needs two people | Shown when the policy's `two_people` rule applies |
| Warnings | For example `no WHERE clause: this changes every row the agent can see`, or `changes no rows: they may not exist, or be outside this agent's scope` |

Writes that the policy approves on its own, under `auto_max_rows`, never need a person, so they are not posted. They still appear in the ledger.

## Approve or deny

Press **Approve** or **Deny**. Lokra records the decision, then updates the message: the buttons go and a line shows the outcome, such as:

```text
Approved by dana (slack)
```

Approving does not run the write. The agent runs it with `execute_write`, and Lokra still refuses it if it would now change more rows than the dry run showed. See [approvals](/docs/approvals).

The same happens when someone decides elsewhere. If a write is approved with `lokra approve`, in the dashboard or in Lokra Cloud, its Slack message closes with that outcome within a few seconds.

## Two-person approvals

When a write needs two people (the agent's `approval` block lists its statement under `two_people`), the message says **Needs two people**. After the first approval it stays open and updates:

```text
Needs two people: approved by dana (slack), one more to go
```

A second, different approver must press **Approve** before the write is approved. If the same person presses it again, only they see a private note:

```text
dana (slack) has already approved w_28cbd037; it needs a different person
```

The two approvals can come from different places, for example one in Slack and one with `lokra approve`. Once both are in, the message closes with both names: `Approved by dana (slack), sam (slack)`.

> **Note:** Lokra tells approvers apart by name, ignoring where the approval came from, so `dana (slack)` and `dana` from `lokra approve` count as one person. A Slack approval is recorded as the Slack username followed by `(slack)`, and `lokra approve` records the `--by` name or the username on that machine, which need not match. Limit who can run `lokra approve` on the Lokra host, so the second approval really comes from a second person.

## Pause and resume from Slack

Every approval message has a **Pause** button for the agent that proposed the write. Pressing it pauses the agent with the reason `paused from Slack`: Lokra refuses its requests at once, and with `--admin-dsn` it also blocks the agent's database login and ends its sessions.

Whenever an agent is paused, from Slack or anywhere else, Lokra posts a notice with a **Resume** button:

```text
ops-bot is paused by dana (slack): paused from Slack. Lokra refuses its requests.

[Resume]
```

When the agent is resumed, from Slack or anywhere else, the notice changes to say it is running again. See [stop an agent](/docs/stop-agents) for what a pause does.

## Who may decide

Only the member IDs in `slack.approvers` can use the buttons: Approve, Deny, Pause and Resume. Anyone else in the channel who presses one gets a private note and nothing changes:

```text
You're not on Lokra's approver list for this database.
```

Being in the channel is not enough, so you can share the channel with people who should see writes but not decide them. Each decision is written to the ledger with the approver's Slack username, so `lokra log` and `lokra report` show who approved what.

The approver list is read when `lokra slack` starts. After you change it, restart `lokra slack`.

## Errors when starting

| Message | Fix |
|---|---|
| `Slack support needs an extra package: pipx install 'lokra[slack]'` | Install the extra, as in step 5. |
| `set LOKRA_SLACK_BOT_TOKEN (xoxb-…) and LOKRA_SLACK_APP_TOKEN (xapp-…)` | Set both environment variables. |
| `add the channel to policies.yaml: slack: {channel: C0123ABCD, approvers: [U0123ABCD]}` | Add `slack.channel` to the policy. |
| `list who may approve: slack: {approvers: [U0123ABCD]} (Slack member IDs)` | Add at least one approver. |

If it starts but nothing appears in the channel, check that the bot is a member of it. For more, see [troubleshooting](/docs/troubleshooting).
