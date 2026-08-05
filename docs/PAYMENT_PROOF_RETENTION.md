# Payment-proof retention

Policy: copied payment screenshots and PDFs are retained for at most 365 days.
Registration, amount, payment method, verification time, provider transaction
ID, and audit-log metadata are retained; the proof media itself is not.

Telegram's Bot API can delete an individual message only during the first 48
hours.  Its long-term auto-delete timer applies to every message in a chat, not
only media.  Therefore payment proofs must use a dedicated review chat:

1. Create a private admin chat used only for payment proofs.
2. Add the bot as an administrator with permission to manage auto-delete.
3. Set `PAYMENT_PROOFS_CHAT_ID` to that chat.  It must differ from
   `EVENTS_CHAT_ID`.
4. Keep `PAYMENT_PROOF_RETENTION_DAYS=365` (the default).

On startup the bot configures the dedicated chat's auto-delete timer and sends
new proofs there.  If the dedicated chat is missing or equals the events chat,
retention is refused and current routing remains unchanged.  Enabling the timer
is not retroactive: legacy proofs in the shared events chat require a one-time
admin-client cleanup after they cross the one-year cutoff.
