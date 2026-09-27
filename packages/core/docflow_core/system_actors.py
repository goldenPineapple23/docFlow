"""
Named system actors (D-159, D-165).

An audit row must never have a blank actor: a blank reads the same as
"unknown". Work that no person did is attributed to one of these, by name.
Each is a `users` row seeded by migration 0028 with the fixed id below. It has
no tenant and no sign-in id, it is inactive (so it can never sign in or be
invited), and it is marked in `users.system_actor`. A database trigger stops
the app role from creating, editing or deleting one; only the table's owner
(migrations) can.

Adding one means a migration that inserts the row, plus a constant here; a
test checks that the two agree.
"""

from __future__ import annotations

from uuid import UUID

# The lifecycle sweep (celery beat): moving a cancelled tenant to `suspended`
# and `pending_deletion` at its effective date (Section 7.14).
LIFECYCLE_SWEEP = UUID("00000000-0000-4000-8000-00000000a001")

# A one-off maintenance script run by the founder or by Claude Code on the
# founder's instruction (for example the 2026-09-26 test-tenant rename).
MAINTENANCE_SCRIPT = UUID("00000000-0000-4000-8000-00000000a002")

# Stands in for a person whose account was deleted with their tenant
# (Section 7.14). One shared actor for everyone: it links back to no one, and
# no old id is kept anywhere.
DELETED_ACCOUNT = UUID("00000000-0000-4000-8000-00000000a003")

ALL: dict[str, UUID] = {
    "lifecycle-sweep": LIFECYCLE_SWEEP,
    "maintenance-script": MAINTENANCE_SCRIPT,
    "deleted-account": DELETED_ACCOUNT,
}
