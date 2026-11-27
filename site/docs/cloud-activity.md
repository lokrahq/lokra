---
title: Activity and export
description: Read the signed ledger from every gate in Lokra Cloud, filter it by kind and date, check the chain, and export it.
---

The **Activity** page shows the signed ledger from every gate in your organisation, newest first. Each row is one entry that a gate wrote to its own ledger: a read, a proposed or decided write, a refusal, a pause. You can filter by kind and date, check that the entries link up, and export what you see as CSV or JSON lines.

Cloud's copy is for reading and searching. Each gate keeps its complete ledger, with signatures, on your server. See [Audit ledger](/docs/audit).

## What each row shows

The page header reads `signed ledger from every gate · kept 7 days`, or `kept 1 year` on the Team plan. The table has five columns:

| Column | What it shows |
| --- | --- |
| # | The entry's sequence number in its gate's ledger. Each gate counts from 1. |
| When | How long ago, such as `12m` or `3h`, for entries from the last 24 hours, and the date and time for older ones. Hover for the full time. |
| Event | The ledger event, such as `read`, `write_proposed` or `stopped`, coloured by kind. |
| Details | The agent, then the most useful detail: the reason or database error, rows read and how many values were masked, rows a write would change or changed, or who decided. |
| Hash | The first 8 characters of the entry's hash. |

## The chain check

Every ledger entry carries its own hash and the hash of the entry before it, so the entries form a chain. Above the table, Cloud checks the entries it has loaded. For each gate, wherever two entries with consecutive numbers are both loaded, it checks that the later one points at the earlier one's hash.

- `chain intact · 48 linked entries checked` means every pair it could check links up.
- `chain broken at entry 1204 on production-db` means that entry does not point at the one before it.

The check grows as you scroll and load more. Entries that are filtered out, or that Cloud never copied, leave gaps that are skipped rather than counted as breaks.

> **Note:** This check covers links between hashes only. Cloud never receives the signatures, so it cannot check them. For a full check of a gate's ledger, signatures included, run `lokra verify-ledger` on the gate.

## Filters

The chips above the table pick a kind of entry. Each entry falls into exactly one.

| Filter | Events |
| --- | --- |
| All | Every entry. |
| Writes | `write_proposed`, `write_partly_approved`, `write_approved`, `write_executed`, `write_rolled_back`, `agent_paused`, `agent_resumed` and `token_revoked`. |
| Blocked | `rejected` (the policy refused the query), `db_refused` (the database refused it), `denied` (a bad token or a missing or wrong purpose) and `write_denied` (a person denied a write). |
| Stopped | `stopped`: a request refused because the agent was paused, its token was revoked or it hit its rate limit. |
| Reads | `read`, `listed_tables` and any other event. |

Pausing and resuming an agent count as **Writes**, but the requests a paused agent makes afterwards count as **Stopped**.

## Date ranges

The menu next to the filters picks a date range. Dates use your device's time zone.

| Range | Shows |
| --- | --- |
| Today | Since midnight today. |
| Last 7 days | Today and the six days before it. |
| Last 30 days | Today and the 29 days before it. Team plan only. |
| Last 90 days | Today and the 89 days before it. Team plan only. |
| All history | Everything your plan keeps: `All history (7 days)` on Free, `All history (1 year)` on Team. |
| Custom dates | From and to dates you pick. Both days are included. Leave either one empty for an open end. |

Every range stops at your plan's history. A custom range that starts earlier shows only what Cloud still keeps. See [How long Cloud keeps activity](#how-long-cloud-keeps-activity).

## Paging

Activity loads 50 entries at a time. More load as you scroll towards the bottom of the table, or choose **Load more**. When there is nothing older, the table ends with a count, such as `312 entries · nothing older in this range`, or `Nothing in this range.` if no entry matches.

## Export

**Export CSV** downloads every entry that matches the current filter and date range, newest first. Cloud shows **Preparing your export** and your browser saves a file named like `lokra-activity-2026-11-27.csv`. Anyone in the organisation can export, whatever their role.

An export holds at most 100,000 entries. If a range holds more, the file has the newest 100,000. Export older date ranges separately to get the rest.

### CSV columns

| Column | Contents |
| --- | --- |
| `ts` | When the gate wrote the entry, in UTC. |
| `gate` | The gate's name in Cloud. |
| `seq` | The entry's sequence number in that gate's ledger. |
| `event` | The ledger event. |
| `agent` | The agent. |
| `on_behalf_of` | The person the agent acted for, from its token. |
| `purpose` | The declared purpose of use, if any. |
| `tables` | The tables involved, separated by `;`. |
| `rows` | Rows read, rows changed, or rows the dry run said would change, whichever the entry has. |
| `write_id` | The write's ID, such as `w_3f9a1c2e`. |
| `decided_by` | Who approved, denied, paused or resumed, such as `dana (cloud)`. |
| `reason` | The agent's reason, the refusal reason or the pause reason. |
| `sql` | The SQL, masked as it is in the ledger. |
| `hash` | The entry's hash. |
| `prev` | The previous entry's hash. |

Any cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`. Spreadsheet apps then show it as text instead of running it as a formula. Cells that contain commas, quotes or line breaks are quoted.

### JSON lines

For every field of every entry, not only the columns above, export JSON lines instead. While signed in, open this address in the same browser:

```text URL
https://cloud.lokra.dev/api/events/export?format=json
```

Each line is one entry as the gate sent it, without its signature, plus `gate_id` and `gate`. The file is named like `lokra-activity-2026-11-27.jsonl`. It follows the same rules as the CSV: newest first, up to 100,000 entries, within your plan's history.

To narrow it, add the same parameters the Activity page uses:

| Parameter | Value |
| --- | --- |
| `kind` | `w` for Writes, `b` for Blocked, `s` for Stopped, `r` for Reads. Leave it out for all. |
| `from` | The earliest time, as an ISO 8601 timestamp such as `2026-11-01T00:00:00Z`. |
| `to` | The time to stop before, in the same form. |

```text URL
https://cloud.lokra.dev/api/events/export?format=json&kind=b&from=2026-11-01T00:00:00Z
```

## How long Cloud keeps activity

| Plan | Cloud keeps |
| --- | --- |
| Free | 7 days |
| Team | 1 year |
| Enterprise | Longer, by agreement |

Activity only ever shows entries inside your plan's window. Once a day, at 03:17 UTC, Cloud deletes entries older than each organisation's plan allows.

If your organisation moves from Team to Free, for example after you cancel, the window shrinks to 7 days at once. Older entries disappear from Activity and exports straight away, and are deleted at the next daily run. Export anything you need before Team ends.

Deleting a gate's entries in Cloud never touches the gate's own ledger. Removing a gate deletes all of its activity from Cloud, but not from the gate. See [Connect a gate](/docs/cloud-gates#remove-a-gate).

### When Cloud stops copying

Each plan copies a set number of agent actions a month: 25,000 on Free, 1,000,000 on Team. Past that, Cloud stops copying new entries until the month resets or you upgrade, and Activity says so:

```text Lokra Cloud
Cloud has copied this month's 25,000 agent actions on Free. Your gates keep every entry in their own ledgers, and approvals and pauses keep working.
```

See [Plans and billing](/docs/cloud-billing#what-happens-over-the-limit).
