# 0004 — E-mail as the MVP delivery channel

Status: accepted (2026-09-26)

## Context

The owner's leagues live on WhatsApp (and Messenger). Neither offers an official, affordable
way for a bot to post in an ordinary group of friends; unofficial libraries risk a ban and
need a second phone number. Discord and Telegram are easy, but the owner's friends do not use
them.

## Options

- **E-mail to the owner**: official, free (Gmail SMTP or Resend free tier), push-notified on
  the phone within seconds.
- **Unofficial WhatsApp library** (Baileys) on a second number: direct delivery, ban risk.
- **Discord / Telegram bot**: rejected — not where the users are.

## Decision

E-mail to the owner for both alerts and the presser; the owner forwards the presser to
WhatsApp by hand. Delivery sits behind an interface, so a WhatsApp adapter can be added
later.

## Consequences

- No chat integration and no chat data in the MVP.
- End-to-end alert latency includes e-mail delivery; it is part of the 60 s measurement.
- WhatsApp delivery waits for a second number (BACKLOG #3).
