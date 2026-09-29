"""
backend/notify — outbound email for the investor platform (Resend).

    from backend.notify import outbox, templates
    outbox.queue(session, templates.deposit_confirmed(...))

Nothing is sent from inside a request. A message is attached to the database
session and handed to the sender only AFTER that session commits; a rollback
discards it. So an email can never announce a deposit, withdrawal or closure
that did not actually happen.
"""
