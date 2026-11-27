---
title: Plans and billing
description: Lokra Cloud's Free, Team and Enterprise plans, what counts as an agent action, what happens at the limit, and how to upgrade or cancel.
---

Lokra Cloud has one price for the whole team. Every plan includes unlimited people, agents and databases. Plans differ in how many agent actions Cloud copies from your gates each month and how long it keeps them. The open-source gate is free whatever plan you choose, and it keeps working on its own if you never use Cloud.

## Plans

| | Free | Team |
| --- | --- | --- |
| Price | $0 | $49 a month, or $490 a year |
| Agent actions a month | 25,000 | 1,000,000 |
| History | 7 days | 1 year |
| People, agents and databases | Unlimited | Unlimited |
| Approvals, pauses and alerts from your phone | Yes | Yes |
| Priority email support | No | Yes |
| Free trial | — | 14 days, if your organisation has never subscribed |

Free needs no card. Prices are in US dollars, before tax. At checkout Stripe shows the price in the buyer's currency and adds sales tax, VAT or GST where it applies; an Australian buyer, for example, sees 10% GST added. The yearly price costs the same as 10 months, which the Billing page shows as **2 months free**.

### Enterprise

Enterprise is for security reviews and larger fleets. It offers higher limits and longer history, payment by invoice, and help answering your security questionnaire. The price is custom: choose **Talk to us** on the Billing page, or email hello@lokra.dev.

## No per-seat pricing

Lokra never charges per person, per agent or per database. More approvers and narrower agents make you safer, so adding them costs nothing. Invite everyone who should decide or watch, and give each agent only the access it needs. See [Team and roles](/docs/cloud-team).

## The Billing page

Open **Billing**, or on a phone tap **More**, then **Billing**. Everyone in the organisation can see it. Only owners can change the plan.

The top card shows:

- Your plan and its state: **No card needed** on Free, **Trial** with the day it ends, **Active** with the day it renews, or **Payment due**.
- **Agent actions this month**, as a count out of your limit, with a bar and the day the count resets.
- **History**, and how many **People**, **Agents** and **Databases** your organisation has.
- **Manage billing**, for owners once the organisation has a subscription.

Below it, the plans sit side by side, with a **Monthly** and **Yearly** switch.

## What counts as an agent action

An agent action is one entry in a gate's signed ledger that Cloud copies: a read, a proposed, approved or executed write, a refused query, a stopped request, or a pause.

- Cloud counts actions per organisation, across all of its gates, per calendar month in UTC.
- An entry counts once. If a gate sends the same entry again, for example after a dropped connection, it does not count twice.
- Decisions made in Cloud are not actions in themselves. The entries they cause on the gate, such as `write_approved` or `agent_paused`, are.
- When you connect a gate for the first time, it sends the entries already in its ledger, and those count too.

The Activity page's filters show the kinds of entry Cloud copies. See [Activity and export](/docs/cloud-activity#filters).

## What happens over the limit

Nothing stops. Approvals, pauses and syncing keep working, and every gate keeps its full ledger. What changes:

- Cloud stops copying new ledger entries until the month resets or you upgrade.
- Agents, pending writes, decisions and phone alerts keep syncing, because they do not depend on ledger entries. You can still approve, deny, pause and resume.
- The Billing page shows how many entries were not copied, with **Your gates still have every one in their own ledgers.**
- The Activity page shows a notice that Cloud has copied this month's actions, with a link to the plans.

The entries Cloud did not copy are still on each gate. Read them there with `lokra log`, `lokra report` or `lokra evidence`. See [Audit ledger](/docs/audit).

## Start a trial or upgrade

Only owners can do this.

1. Open **Billing**.
2. Under **Plans**, choose **Monthly** or **Yearly**.
3. On the Team plan, choose **Start 14-day trial**. If your organisation has subscribed before, the button reads **Upgrade to Team** and there is no trial.
4. Complete Stripe Checkout. It accepts promotion codes.

Stripe brings you back to the Billing page. The plan changes to Team as soon as Stripe confirms the subscription, usually within seconds. During a trial, the plan card reads **Trial** with the day it ends.

People who are not owners see **Ask an owner to upgrade** on the Team plan instead of a button.

## Manage or cancel

Owners choose **Manage billing** on the Billing page. It opens Stripe's billing portal, where you can update your card or cancel.

- **Payment due** means a payment failed. Team stays on while you update your card in **Manage billing**.
- **If payment stops altogether**, the organisation is treated as Free, but Cloud keeps its year of history for two weeks past the paid period, so paying again within that time brings it all back.
- **Cancelling** keeps Team on until the end of the period you paid for. Then the organisation moves to Free.

> **Warning:** When an organisation moves to Free, Cloud keeps only 7 days of activity. Older entries disappear from Activity at once and are deleted at the next daily clean-up, at 03:17 UTC. Export them first if you need them. See [Export](/docs/cloud-activity#export).

Moving to Free does not touch your gates, agents, people or their roles, and it never touches the ledgers on your gates.

## Questions

### Do I need a card for Free?

No. Sign in with GitHub and connect your gates. Free has no time limit.

### Does adding a gate or a person change the price?

No. The price depends only on the plan. Gates, agents and people are unlimited on every plan, though each gate's ledger entries count towards your agent actions.

### Who handles payment details?

Stripe. Checkout and the billing portal are Stripe pages, so Cloud never sees your card number. Any owner can open **Manage billing**.
