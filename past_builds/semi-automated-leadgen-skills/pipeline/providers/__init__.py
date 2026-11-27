"""Provider interfaces and their mock twins.

The rule this package exists to enforce: no stage module ever constructs an HTTP
client itself. It asks a factory here, the factory reads the environment, and a
missing key silently yields the mock. See base.py for why.
"""

from .base import Provider, ProviderError  # noqa: F401
