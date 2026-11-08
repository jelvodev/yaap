"""Tracing: what an agent run did, step by step, and what the model saw.

- `recorder`: the trace recorder a run carries in its `RunServices`; it writes one `RunStep` per model call and per
  tool call.
- `provider`: the wrapper at the common entry of the provider adapters that records model calls.
- `snapshots`: the content-addressed store of context snapshots under `data/traces/`.
- `steps`: reading a run's steps and a conversation's run tree, for the timeline.
- `context_view`: what a model call saw, grouped by source with sizes, and one part's content on demand.
"""
