from tests import database_guard

# First, before any test module can open a connection (D-211).
database_guard.check()
