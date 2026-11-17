# reporter: findings with source evidence

Reporter supports investigations, snapshot comparisons and bounded retrieval of recorded CLI/config/log fragments. It helps connect a statement to the supplied source; it does not create missing observations or validate an answer merely by returning a successful tool result.

## Retrieve focused evidence

`query_evidence` requires `source` (`command_output`, `config`, `syslog`) and a `pattern`. Scope with `device`, `commands`, `ids` and an explicit `snapshot` where appropriate. Add `limit`, `page` or `offset` for bounded retrieval. For example, after the synthetic import the [offline walkthrough](../offline-data.md) retrieves `Hostname` from R1 / `show version` using the saved snapshot ID.

Inspect the returned matches, source identities/hashes, excerpt bounds, `total`, `total_is_complete`, `has_more` and paging metadata. A match count is not a device count. A body excerpt can itself be shortened; paging can recover omitted rows but cannot restore a shortened body. Use narrower evidence questions rather than full raw-output dumps.

A date filter (`date_from`/`date_to`) applies only to candidates with known collection dates in range. `date_unknown_candidates`, `date_exclusions` and `date_scope` expose excluded unknown/invalid/conflicting-time candidates after other metadata filters and before body matching. Those exclusions are not match counts; their bodies were not evaluated. Do not add them to a displayed candidate count to invent a denominator. Report exclusions even when some known-date matches exist.

`no_dated_candidates` plus unknown-time exclusions leaves the date question unanswered. An empty page beyond the end is also different from an empty result set. Keep filters and snapshot fixed when following `next_offset`/pages. See [script result semantics](../reference/scripts.md).

## Build a report from checked results

Use `query_batch` for supported metadata/health summaries, `describe_table` for unfamiliar relevant views and `diff_snapshots` for key-set comparisons. `read_change_plan` reads an existing draft. These helpers do not include `format_and_export`, a simulator or a generic Markdown report-writing endpoint. The synthetic example writes its report using ordinary reviewed Python file I/O; an AI host needs its own authorized writing tool.

A useful report includes scope and snapshot, collection-time provenance, inventory vs observed membership, findings with device/value/source, missing sources, truncation and recommendations. Label graph impact as graph evidence. Preserve exact technical tokens when passing a report to [writer](writer.md) for formatting. Every conclusion must be supported by returned evidence, including explicit unknowns and parser gaps.
