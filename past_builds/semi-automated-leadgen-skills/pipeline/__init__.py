"""The real lead-generation pipeline, one module per game level.

source_bakeries -> enrich -> qualify -> outreach, chained by run_pipeline.
One list of 25 Chicago bakery listings goes in; every stage works on that same
list. Every external call goes through pipeline.providers; each provider has a
mock twin selected automatically when its API key is unset, so the whole thing
runs — and the whole test suite passes — with no accounts and no spend.
"""

__all__ = ["schema", "source_bakeries", "enrich", "qualify", "outreach",
           "run_pipeline"]
