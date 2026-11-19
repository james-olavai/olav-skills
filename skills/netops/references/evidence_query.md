# Evidence query contract (scoped drilldown, paging, failure)

Shared by `analyzer` and `reporter`. Standalone pack copy:
`skills/netops/references/evidence_query.md`. The two entry points are different
tools with different arguments; neither accepts the other's names.

| Source of recorded text | Entry point | Where it lives |
|---|---|---|
| `command_output`, `config`, syslog | `query_evidence` (reporter script) | authority `reporter/scripts/query_evidence.py`; standalone `skills/reporter/scripts/query_evidence.py` |
| syslog | `search_logs` | `analyzer/scripts/search_logs.py` |

Use `olav-skills run reporter query_evidence` or
`olav-skills run analyzer search_logs`, with one JSON object on stdin.
`query_evidence` validates its keys
and rejects an unknown one with a JSON error listing `accepted_args`.
`search_logs` has no such guard: an unsupported key raises a Python
`TypeError` at its entry point (non-zero exit, no JSON), so pass only the keys
listed below and fix typos from that traceback — an offset/device/commands key
is not accepted there.

## Scope: narrow only inside the user's question

Metadata filters (device, command, snapshot, date, IDs) are applied **before**
any body match. Narrow them only where the user's authorization and question
allow it. If the question is broad and the broad query cannot finish, report the
partial / unfinished scope with its failure; never quietly swap in a narrower
filter and then claim the answer is exhaustive. If a filter is unsupported for
the source, say so instead of dropping it.

## query_evidence — accepted arguments

`source` (required): `syslog` | `command_output` | `config`.
`pattern` (required): non-empty case-insensitive substring of the body.
`device`: case-insensitive substring of the device name (`raw_output_store.device_name`, or syslog `host`).
`snapshot`: exact `raw_output_store.snapshot_id` label (last-writer label, not a partition key). The key is accepted for every source, but the syslog path does not forward it to the syslog search: for `source="syslog"` it has no scoping effect. Do not present it as a syslog filter; use `date_from`/`date_to` or `time_range` for syslog scope.
`commands`: exact, case-sensitive command strings, at most 50 values, non-empty and bounded in length.
`ids`: exact `source_id` values as returned in `matches[].source_id`, at most 50.
`time_range`: syslog only, `last_1h` / `last_24h` / `last_7d` (default window 24 h).
`date_from`, `date_to`: inclusive `YYYY-MM-DD` collection-date scope.
`page` (1-based), `offset` (non-negative), `limit` (1..50, default 50), `deadline_ms` (default 15000).

Source-specific rules:

- `source="config"` means `raw_output_store` rows whose command matches
  `running-config` or `startup-config`; it is a command-class filter, not a
  config parser.
- `source="syslog"` rejects `commands` and `ids` with
  `error_kind="invalid_argument"` — those scope raw command output only. Syslog
  has `device` (host substring), `time_range`, `date_from`/`date_to`; `snapshot`
  is accepted syntactically but has no effect on the syslog query.
- `commands` and `ids` are exact: `commands` compares catalogue command strings
  (`IN`), `ids` compares the record identity `query_evidence` returns. They are
  not substring filters over the body.

### Matching and identity

`pattern` and `device` are the only substring matches. Both are evaluated as
SQL `ILIKE '%value%'`, so `%` and `_` are live wildcards: they are not escaped,
and a literal `%`/`_` in the value matches more than the literal text. Treat
them as LIKE patterns when the user's term may contain those characters.
`snapshot`, `commands` and `ids` are equality. `source_id` is either the store's real record id
(`identity_scope="record_id"`), a hash of the `(snapshot_id, device_name,
command)` key (`"record_key"`), or `"record_id_with_record_key_fallback"` when
some rows have a NULL id (`identity_fallback` names the rule). One key can carry
several bodies; `content_hash` separates them, and paging orders by the key,
then `content_hash`, then `source_id`, so rows cannot shift between pages.
`source_id` values returned for real-id stores carry the `rec_id_` prefix
(`rec_id_<id>`); the prefix keeps them distinct from key hashes. `ids` must be
the exact value returned in `matches[].source_id`, including that prefix — the
bare database id is not the accepted token.

`match_line`, `excerpt_line_start` and `excerpt_line_end` are real line numbers
inside the record. For syslog, one message is one record: `source_id` is
`log_<content_hash>`, `match_line` is 1 and `identity_scope` is
`syslog_content_hash`.

### Date clocks

A date filter needs a real collection-time basis. Explicit collection columns
(`collected_at`, `captured_at`, `snapshot_date`) are used when present; a bare
`YYYY-MM-DD` `snapshot_id` label is the accepted *derived* basis
(`collection_basis_status="derived"`), the same label `temporal.collected_at`
exposes. `ingested_at` and `updated_at` are never capture time. If no basis
exists, the call fails `schema_drift` rather than guessing. Syslog dates filter
the syslog event timestamp and replace the relative `hours` window.

Unknown, invalid or conflicting raw collection time is **excluded after the
other metadata filters and before the date body evaluation**. Those candidates
are not body matches: their bodies were not evaluated. `date_unknown_candidates`
and `date_exclusions` (`invalid_value`, `conflicting_capture_time`,
`no_capture_time`) disclose them, and `date_scope.status` records the case
(`partial_unknown`, `unknown_only`, `known_outside_range`, `known_only`,
`unknown_not_exposed`, or `not_applicable`). The pre-date candidate denominator
is not returned (`excluded_candidates_total=null`,
`excluded_candidates_total_status="unknown_not_exposed_by_helper"`). Never use
`candidate_total + date_unknown_candidates` as a denominator — records with a
known date outside the range are excluded too. `date_scope.status="not_applicable"`
for syslog (`syslog_time_semantics`) or for a call without a date filter
(`no_date_filter`) is a different statement from an unknown-candidate exclusion:
nothing was computed or withheld there, and no denominator may be presented.
`zero_reason="no_dated_candidates"` means no candidate has a known date inside
the range; when unknown-time exclusions exist the date question is unanswered,
so never report it as "no recorded evidence".

### Paging, budgets and counts

`limit` is the page size (1..50); `page` is 1-based; an explicit `offset`
overrides `page` (`page` is recomputed as `offset // limit + 1`) so a page
shortened by the character budget can be resumed without gaps or repeats. Keep
the same source, pattern, device, snapshot, commands, ids and dates while
paging, and keep the same snapshot/dataset.

`has_more` is true while `offset + returned < total`; when it is true (or rows
were omitted by the character budget) `next_offset` is the exact offset to
resume from. A page that is empty only because `offset` is at or past `total`
is a paging artifact and carries a `hint` saying so — it is not zero matches.

Per call, returned text is bounded at 12 000 characters. The row that would
overflow is kept with cut text (`char_budget_truncated`, `char_budget_fields`,
`excerpt_truncated`); rows after it are dropped and counted in
`budget.rows_omitted_by_char_budget`. Resuming from `next_offset` recovers the
dropped rows. It does **not** recover a shortened body: every returned body is
an excerpt (`excerpt_truncated` rows are ~1 200-character excerpts around the
match, 800 when there is no match position, for raw output and syslog alike).
Narrowing the scope does not restore the full body either — a different
pattern/device/ID/date may return a different bounded excerpt window, and the
full body never leaves the database. Report the excerpt as an excerpt, with its
`body_chars` and `content_hash` (`message_chars` for syslog).

Counts are separate facts, never interchangeable:

- `total` — complete body-match count for the whole filtered scope (`total_scope="filtered_scope"`, `total_is_complete=true`); with a date filter it covers known-date candidates only.
- `candidate_total` — complete metadata candidate count after **all** supplied metadata filters, including an applied date range; `date_scope.known_dated_candidates` is this same number.
- `returned` — rows in this page only.
- `excerpt_truncated_rows` — returned rows whose body is an excerpt.
- `date_unknown_candidates` / `date_exclusions` — excluded candidate records, not matches.
- `path` — `candidate_body`, `full_scan` (candidate set above the internal budget: one complete count/page, still not a sample) or `metadata_only`.

Never present `returned`, a page size, or a sample as the total.
`zero_reason` distinguishes `store_empty`, `no_candidates`, `no_body_match`,
`source_missing` and `no_dated_candidates`.

## search_logs — accepted arguments

`query` (case-insensitive message pattern; `""` means no body filter),
`hours` (default 24), `severity` (`emergency`/`alert`/`critical`/`error`/
`warning`/`notice`/`info`/`debug`, case-insensitive), `host` (host pattern),
`limit`, `page` (1-based), `date_from`, `date_to`. There is **no** `offset`,
`commands`, `ids` or `device` alias here: `offset` is derived as
`(page - 1) * limit`, device scope is `host`, and command/ID scope does not
exist in this tool.

`query` and `host` are evaluated as SQL `ILIKE '%value%'`, so `%` and `_` are
live wildcards: they are not escaped, and a literal `%`/`_` in the value makes
the match broader than the literal text. Treat them as LIKE patterns, not as
verbatim substrings, when the user's term may contain those characters.

`limit` is clamped to 1..500; when omitted it takes the model-tier default
(small 20 / medium 50 / large 100, fallback 50). `date_from`/`date_to` are
inclusive `YYYY-MM-DD` and replace the relative `hours` window; a reversed range
or a malformed date is rejected. An invalid severity is rejected with the valid
list. Rows that share a timestamp keep a stable order across pages.

Continuation uses the returned header, which carries the complete match count
and the page range: `Found <total> syslog records (...); showing <from>-<to> of
<total> (page <n>, limit <n>, more pages | no more records)` — fetch `page + 1`
while it says `more pages`. A page past the end says `(page <n> is past the end
of <total> matches)` and is not an empty result. `total` is the complete count
for the filters; `path=candidate_body|full_scan` says which counting path ran,
and only the returned page is a sample of rows.

Messages are bounded excerpts around the match (~400 characters after ~120
characters of lead-in); a truncated row is annotated with `full_chars` and
`full_md5`, so paging never reveals the rest of that body.
Distinguish "No log records found." (missing store or no matches, with the
parenthesised suffix naming the case) from `No log records found matching the
criteria.`, from `Permission denied`, `Log store schema drift`,
`Log query budget exceeded` and `Error querying logs`. A zero-row relative
window shorter than 24 h is widened to 24 h and reported as a clock-skew
fallback; that widening does not apply to explicit date ranges.

## Failures, retries and budgets

`query_evidence` errors keep their category and message:
`error_kind` ∈ `invalid_argument`, `source_missing`, `permission_denied`,
`schema_drift`, `sql_error`, `budget_exceeded` (plus `duplicate_call_budget` for
the third identical call), with `source`, `detail` and `recoverable`.
`search_logs` reports the same classes in text. In both cases:

- Report the failure and the unfinished scope. A failed query is never zero
  matches and never "no recorded evidence".
- Never disable or raise a safety budget (deadline, character budget, row/page
  limits, tier clamp), never loop or retry indefinitely, and never fall back to
  dumping raw bodies.
- One bounded corrective retry is allowed only for an actual correctable issue
  (mistyped key, invalid date, offset past the end, wrong source) and only
  inside the same authorized scope and budget.
- On `budget_exceeded` / `Log query budget exceeded`, stop and report, or ask
  for an authorized smaller scope (narrower device/command/date/ID or a smaller
  page). Do not re-run the same expensive query hoping for a different result.
- The investigation stopping rule (≤ ~8 evidence queries) limits *how many*
  queries are worth issuing. It is not the execution resource budget
  (wall-clock deadline, memory, character/row limits, result size) and cannot be
  cited to excuse a timeout, a truncated page or a partial result. Report those
  as unfinished.
