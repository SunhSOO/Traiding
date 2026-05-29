"""External data ingestion adapters.

Every subpackage here is a one-way pipe from an outside source into
our DB. Adapters are responsible for:
- HTTP/API specifics of the source
- Rate limiting and backoff
- Normalising to our internal schema (markets/tickers, columns,
  units, time zones)
- Stamping every row with an `as_of_ts` reflecting when the data
  became known

Loaders compose adapters + database session into idempotent upserts.
Re-running a loader for an already-fetched window must be safe.
"""
