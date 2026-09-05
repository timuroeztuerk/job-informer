"""Original job sources, durable retrieval, and replayable extraction.

Save response bytes before parsing; keep network I/O outside transactions.
New extractors read a saved source and append through store.save_extraction.
Bump the extractor version for changed output and the schema version for changed
meaning/shape. Missing fields stay unknown; include evidence for derived values.
Extraction never changes flags, favorites, relevance, or sightings.
"""
