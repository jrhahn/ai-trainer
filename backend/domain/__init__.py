"""Pure domain rules: no database, no I/O, no model calls (ai-trainer-ops#304).

The lowest layer above config. schemas, crud and the services all read these
rules, so they import nothing from the backend but config and each other.
"""
