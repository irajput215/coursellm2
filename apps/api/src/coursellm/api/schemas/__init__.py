"""Transport schemas.

Response models are written independently of the ORM models on purpose. A schema
that inherits from a database model publishes whatever that model later gains —
which is how the previous implementation ended up returning bcrypt password
hashes from ``/auth/me`` and the registration response.
"""
