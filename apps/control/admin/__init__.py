"""The admin console, on its own origin (admin.<host>): how the agent performs across every call, the calls one by one,
the users. Sign-in is a password (argon2id) and a TOTP code; admins are made on the server with the CLI (no default
password). Every sign-in, every look at personal data (an unmasked number, a transcript) and every change is written
to audit_log.
"""
