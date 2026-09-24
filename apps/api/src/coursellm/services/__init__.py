"""Use cases.

Behaviour lives here so that it is reachable from a router, a worker, the CLI and
a test without going through HTTP. Routers translate transport to a call; they do
not contain rules.
"""
