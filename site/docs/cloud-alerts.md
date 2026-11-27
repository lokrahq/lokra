---
title: Approve from your phone
description: Add Lokra Cloud to your phone, turn on alerts, and approve, deny or pause agents from anywhere.
---

Lokra Cloud works in any current browser, and on a phone it can live on your Home Screen like an app. With alerts on, your phone tells you when an agent's write is waiting, even with Lokra closed. You can then hold to approve, deny, or pause the agent, and watch the decision reach the gate.

## Add Lokra to your Home Screen

### iPhone and iPad

On iPhone and iPad, alerts only work once Lokra is on your Home Screen. This needs iOS or iPadOS 16.4 or later.

1. Open [cloud.lokra.dev](https://cloud.lokra.dev) in Safari and sign in.
2. Tap Share in the browser toolbar.
3. Scroll and tap **Add to Home Screen**.
4. Open Lokra from your Home Screen like any app.

Lokra shows these steps too: tap **More**, then **Add to Home Screen**. If Lokra asks you to sign in when you first open it from the Home Screen, sign in with GitHub again.

### Android

Alerts work in Chrome on Android without installing anything. To get a Home Screen icon as well, tap **More**, then **Add to Home Screen**, and accept your browser's prompt. The option appears when your browser offers to install Lokra.

### Desktop browsers

Alerts work in desktop browsers without installing. If your browser can install Lokra as an app, an install button (**Install as an app**) appears at the bottom of the left rail.

## Turn on alerts

Turn alerts on separately on each device where you want them.

1. On a wide screen, click the bell at the bottom of the left rail, next to **Sign out**. On a phone, tap **More**, then **Turn on alerts**.
2. Allow notifications when your browser asks.
3. Cloud sends a test alert to this device:

```text
Alerts are on
Lokra will tell you here when an agent's write needs you.
```

Cloud then confirms with **You'll get an alert on this device, even with Lokra closed**. If it says **You'll get an alert while Lokra is open** instead, this browser could not register for push alerts. You will only get alerts while Lokra is open in a tab or window.

On an iPhone or iPad where Lokra is not on the Home Screen, Cloud shows **Alerts need the app** instead, with the steps above.

The bell, or the item under **More**, always shows where things stand:

| Label | Meaning |
| --- | --- |
| Turn on alerts | You have not allowed notifications on this device yet. |
| Alerts on | This device gets alerts. |
| Alerts off | Notifications are allowed, but you switched Lokra's alerts off on this device. |
| Alerts blocked | Your browser or phone blocks notifications from Lokra. See [If alerts are blocked](#if-alerts-are-blocked). |

## What an alert says

When a write starts waiting, the alert names the agent, the statement and the tables. Below that come the agent's reason, the gate, the number of rows the dry run says would change, and **needs two people** if the write needs a second approver:

```text
support-bot wants to UPDATE orders
Customer asked for a refund · production-db · 3 rows · needs two people
```

If the agent gave no reason, the alert says **No reason given**. If several writes start waiting on one gate at the same moment, you get one alert for all of them, such as **3 writes are waiting for you**, **From production-db**.

Alerts never contain SQL or any values from it. To see the SQL, tap the alert. It opens the Inbox, where the SQL is shown masked.

Cloud sends each alert once, when it first sees the write waiting. If your device is offline, the push service holds the alert for up to an hour.

## Who gets alerts

Cloud sends alerts to every owner and approver in the organisation who has turned them on, on each device where they did. Alerts cover every gate in the organisation. Viewers cannot decide, so Cloud does not send them write alerts. See [Team and roles](/docs/cloud-team).

## Switch alerts off

Click the bell, or tap **More**, then **Alerts on**. The label changes to **Alerts off**, and this device stops getting alerts. Your other devices, and other people, are not affected. Turn them back on the same way; Cloud sends the test alert again.

Signing out also switches alerts off on that device, so a shared or old phone stops getting them.

## If alerts are blocked

If you once refused notifications, the browser remembers, and only its settings can undo that. Clicking the bell then opens **Alerts are blocked**, with the steps for your device:

| Device | How to allow alerts again |
| --- | --- |
| iPhone or iPad | Open the Settings app, go to Notifications, then Lokra, and turn on Allow Notifications. |
| Android, Lokra on the Home Screen | Press and hold the Lokra icon, open App info, then Notifications, and allow them. |
| Android, in the browser | Tap the icon left of the address bar, open Permissions, and allow Notifications. |
| Firefox | Click the icon left of the address bar and clear Notifications: Blocked, then reload. |
| Safari on a Mac | Open Safari, then Settings, then Websites, then Notifications, and set cloud.lokra.dev to Allow. |
| Chrome, Edge and other browsers | Click the icon left of the address bar, open Site settings, set Notifications to Allow, then reload. |

Then click the bell again to turn alerts on.

## Act on a request

Each request in the Inbox shows the statement and tables, the gate, the agent and who it acts for, the agent's reason, the masked SQL, and how many rows the dry run says would change. Viewers see **view only** instead of the buttons.

### Hold to approve

Press and hold **Hold to approve**. The button fills and reads **Keep holding**; after about a second the approval is sent. Let go early and nothing happens, so a stray tap in your pocket cannot approve a write. Cloud confirms with **Approved · sending to production-db**.

On a keyboard, hold `A`, or focus the button and hold Space or Enter.

### Deny

Tap **Deny**. It takes effect at once, without a second step, and Cloud confirms with **Denied**.

### Pause the agent

Each request has a **Pause** button for its agent. On a wide screen it names the agent, such as **Pause support-bot**. It pauses the agent at once, without a second step, with the reason `paused from Lokra Cloud`. Lokra then refuses that agent's requests until someone resumes it.

On the **Agents** page, **Pause** first asks **Pause support-bot?** and lets you add a reason before you choose **Pause agent**. To resume a paused agent, use **Hold to resume** on the same page.

If the gate runs `lokra cloud` with `--admin-dsn`, a pause also blocks the agent's database login and ends its open sessions. See [Connect a gate](/docs/cloud-gates#why-admin-dsn-matters-for-pauses).

### Watch it reach the gate

After you decide, the buttons give way to three steps:

```text
Sent ── Gate received ── Approved
```

- **Sent**: Cloud has queued your decision for the gate.
- **Gate received**: the gate picked it up on its next sync, usually within a few seconds.
- The outcome: **Approved**, **Denied**, **Paused** or **Resumed**. For a write that needs two people, the first approval ends at **1 of 2 approved**. If the gate could not apply it, the last step reads **Failed**, with the gate's reason underneath, for example because someone already decided the write in the terminal.

A few seconds after the outcome, the request leaves the list. Decided writes move to **Decided today** at the bottom of the Inbox, with their status and who decided them.

If a decision stays at **Sent**, the gate is not syncing. Check that it is live on the **Gates** page. The decision waits in Cloud, and the gate applies it when it reconnects.

## Two-person approvals

Some writes need two different people to approve them. The [approval rules](/docs/approvals) in the gate's policy decide which ones. In the Inbox such a write shows **Needs two people**, and its alert ends with **needs two people**.

1. The first approver holds to approve. The steps end at **1 of 2 approved**.
2. The request stays in everyone's Inbox and reads **Needs two people: approved by dana (cloud), one more to go**.
3. The person who approved sees **You approved · waiting for one more person** in place of the approve button. They can still deny or pause.
4. A second owner or approver holds to approve, and the write is approved.

The gate refuses a second approval from the same person, so one person cannot approve twice from Cloud. Approvals from the terminal, the dashboard and [Slack](/docs/slack) count towards the same two.
