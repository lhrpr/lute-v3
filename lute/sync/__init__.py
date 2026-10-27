"""
Multi-device sync.

Lute runs locally on each device (desktop shell, iOS host) against its own
SQLite file, and a hosted instance acts as the meeting point.  This package
carries changes between them so every device can be edited offline and
reconciled later.

Unrelated to words.WoSyncStatus, which means "this term follows its parent's
status".  See lute/db/schema/migrations_repeatable/trig_synclog.sql.
"""
