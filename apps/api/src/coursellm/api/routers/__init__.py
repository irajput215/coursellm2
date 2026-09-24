"""HTTP routers, one module per domain.

Routers own transport concerns only: decoding, validation, status codes and
serialisation. They delegate to ``coursellm.services`` for behaviour, so that
no business rule can be reached only through HTTP.
"""
