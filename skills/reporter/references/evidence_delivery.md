# Offline evidence delivery

In 0.2, use `olav-skills run reporter evidence_bundle` with one JSON object on stdin
and an explicit absolute `project` on a local POSIX filesystem.

- Put individually approved, reviewed UTF-8 JSON, CSV or Markdown under the
  existing `exports/evidence_sources/` directory. Pass those exact
  project-relative paths in `sources`. Package only the report and the evidence
  artifacts approved for delivery.
- Create `exports/evidence_bundles/` if needed. Set `operation` to `create`
  and `bundle` to a fresh basename ending in `.zip`. Save the returned
  `manifest_sha256` through a separate trusted channel if recipients need a
  known-good digest. The operation preserves original artifact bytes.
- With `operation: verify`, select the project containing the ZIP and its
  basename. An independently obtained `expected_manifest_sha256` pins the
  exact manifest. Otherwise verification checks internal consistency.
- For `operation: recheck`, list the same exact authorized paths again.
  Check `integrity` independently from `freshness`. Changed or deleted
  files make the bundle `stale`. An unavailable or unsafe source makes the
  result `unknown` if no stale source was observed. Choose a new bundle name
  when you prepare a revised delivery.

Report the bundle scope as the selected files and collection time as unknown.
A verified archive preserves reported claims and known truncation markers
as evidence to inspect. The digest does not establish those claims, collection
coverage, or the identity of the author. Unchanged freshness compares file
bytes at the authorized paths during recheck. Inputs outside that selection
and the state of the network require separate evidence.
