---
title: Team and roles
description: Invite people to your Lokra Cloud organisation by GitHub username, and what owners, approvers and viewers can each do.
---

Everyone in a Lokra Cloud organisation sees the same gates, agents, requests and activity. What they can change depends on their role: owner, approver or viewer. You invite people by their GitHub username, and they join when they sign in with GitHub.

## Your organisation

The first time you sign in to [cloud.lokra.dev](https://cloud.lokra.dev), unless someone has invited you, Cloud creates an organisation named after your GitHub username and makes you its owner. Your organisation and role appear under the Lokra Cloud name in the left rail, such as `dana · owner`. On the Team plan, the plan name follows, such as `dana · owner · Team`.

## Roles

| What you can do | Owner | Approver | Viewer |
| --- | --- | --- | --- |
| See the Inbox, Agents, Activity, Gates, Team and Billing pages | Yes | Yes | Yes |
| Export activity as CSV or JSON lines | Yes | Yes | Yes |
| Approve and deny writes | Yes | Yes | No |
| Pause and resume agents | Yes | Yes | No |
| Get phone alerts when a write is waiting | Yes | Yes | No |
| Create gate tokens and remove gates | Yes | No | No |
| Invite people, with any role | Yes | No | No |
| Start a trial, upgrade and manage billing | Yes | No | No |

The Team page sums it up: approvers decide and pause, and viewers only watch.

Cloud checks the role on the server for every action, not only in the page. If someone tries anyway, Cloud refuses with a message such as:

| Action | Message |
| --- | --- |
| A viewer approves, denies, pauses or resumes | `your role can view but not approve or pause` |
| A non-owner creates a gate token | `only owners can add gates` |
| A non-owner removes a gate | `only owners can remove gates` |
| A non-owner invites someone | `only owners can invite` |
| A non-owner starts checkout | `only owners can change the plan` |
| A non-owner opens Manage billing | `only owners can manage billing` |

In the page, people without the role do not see those controls. Viewers see **view only** on each request. Approvers and viewers do not see **Connect a database** or **Remove gate** on the Gates page, or the invite form on the Team page. On the Billing page they see **Ask an owner to upgrade**.

### Choosing roles

- **Owner** for the people who run Lokra and pay for it. Keep this group small: owners can connect and remove gates, and removing a gate deletes its history in Cloud.
- **Approver** for anyone who should decide on writes and be woken by an alert, such as on-call engineers and data owners.
- **Viewer** for people who need to see what agents do without acting on it, such as auditors and security reviewers.

Lokra charges nothing per person, so you can give each person the narrowest role that fits.

## Invite someone

Only owners can invite.

1. Open **Team**. On a phone, tap **More**, then **Team**.
2. Under **Invite by GitHub username**, type their GitHub username. A leading `@` is fine.
3. Choose a role: **Approver** (the default), **Viewer** or **Owner**.
4. Choose **Invite**.

Cloud confirms with **Invited**. The invite appears in the list with their username and role, marked `invited by <you> · joins on first sign-in`.

If the username is not valid, Cloud says `enter a GitHub username`. To change the role on an invite that is still pending, invite the same username again with the new role.

## Accept an invite

Cloud does not send an email, so tell them yourself. The invited person:

1. Opens [cloud.lokra.dev](https://cloud.lokra.dev).
2. Chooses **Continue with GitHub** and signs in with the GitHub account you invited.

Cloud matches the invite to their GitHub username, ignoring upper and lower case, and adds them to your organisation with the role you chose. They land in the Inbox and see your gates straight away. The invite then leaves the list and they appear as a member.

Cloud asks GitHub only to read their profile (the `read:user` scope), and keeps their username, name and avatar.

> **Note:** Each person sees one organisation in Cloud: the first one they joined. Someone who signed in on their own before you invited them already owns an organisation of their own, and stays in it. Invite people before they first sign in.

## Removing people and changing roles

Cloud has no way yet to remove a member, change a member's role or cancel a pending invite from the Team page. Choose roles with that in mind, and start people as viewers or approvers rather than owners.

## How people appear in the ledger

When someone decides in Cloud, the gate records it in its signed ledger under their GitHub username followed by `(cloud)`, such as `dana (cloud)`. The same name appears on the Agents page for a paused agent (`paused by dana (cloud)`), in Cloud's Activity page (`by dana (cloud)`) and in the `decided_by` column of exports. See [Lokra Cloud](/docs/cloud#how-decisions-come-back).

A write that needs two people needs two different names. One person cannot approve it twice from Cloud. See [Two-person approvals](/docs/cloud-alerts#two-person-approvals).

## No per-seat pricing

Every plan includes unlimited people, agents and databases. In the words of the Billing page: more approvers and narrower agents make you safer, so Lokra never charges for them. Plans differ only in how much activity Cloud keeps and for how long. See [Plans and billing](/docs/cloud-billing).
