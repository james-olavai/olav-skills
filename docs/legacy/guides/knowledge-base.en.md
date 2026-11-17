# Knowledge Base (Unified Knowledge Store)

OLAV's knowledge system is built on the **Unified Knowledge Store (UKS)** — a single LanceDB table (`memory`) that combines documents, agent memories, and graph-linked knowledge. This replaces the legacy KB system and enables semantic search across all knowledge types in one query.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-21 | Knowledge base indexes documents and supports semantic search (vector + BM25 hybrid) | ✅ v0.10.0 |
    | C-L3-UKS | Unified Knowledge Store: single LanceDB table for documents, memories, and graph nodes | ✅ v0.13.0 |

---

## Use Cases

- "What is the standard procedure for BGP failover?"
- "Where are the disaster recovery steps?"
- "What was the solution used last time for this issue?"

No need to dig through Confluence, search the Wiki, or hunt through emails — just ask OLAV.

---

## Importing Documents

Supported formats: `.md` (Markdown), `.pdf`, `.txt`. Place files in the `.olav/knowledge/` directory (Obsidian vault), then sync them into the UKS:

```bash
olav kb sync          # sync .olav/knowledge/ → LanceDB (incremental)
olav kb import FILE   # import a single file directly
```

Check status:

```bash
olav kb status
```

---

## Searching the Knowledge Base

Ask questions directly in natural language:

```bash
olav "What does our Runbook say about BGP failover?"
olav "Search for DR procedures"
```

OLAV returns the most relevant snippets and generates a comprehensive answer using the LLM. The `olav_recall_memory` tool searches the unified `memory` table, returning results across documents, agent memories, and graph nodes.

---

## Benchmarking recall — `olav kb bench`

`olav kb bench` is a **domain-agnostic self-recall benchmark**: it queries each sampled memory with its **own** text and checks whether that entry comes back in the top-K. A healthy embedder + index returns the entry at rank 1 (~0 distance); a low self-recall@1 signals a degraded embedder or a dimension / index problem.

```bash
olav kb bench                 # sample 40 memories, top-5
olav kb bench --sample 100 --top-k 3
olav kb bench --json
```

Sample output:

```
KB self-recall bench (n=40, top-5):
  self-recall@1=98%  @3=98%  @5=98%
  by category:
    usage_guide  n=22  @1=96%  @5=96%
    reflection   n=5   @1=100%  @5=100%
```

!!! note "No golden set required"
    Unlike the paraphrase→intent hit-rate probe (an integration test that needs a fixed golden set), `kb bench` needs no golden data, so it runs in any install. It also surfaces **near-duplicate memories**: two near-identical rows out-rank each other at rank-1, dropping @1 — a useful signal that the store has redundant entries.

---

## How It Works

The UKS stores all knowledge in a single LanceDB `memory` table with metadata fields (`origin`, `tags`, `source_uri`, `node_type`). This enables unified retrieval across document types:

```
Document (.md/.pdf/.txt)
    → chunk (~512 chars, sentence-aware)
    → vector embedding
    → INSERT INTO memory (origin="knowledge", node_type="document", ...)

Agent memory (from L1 capture / L2 patterns / directive guides)
    → INSERT INTO memory (origin="agent"|"directive", node_type="event"|"lesson", ...)

Graph node
    → INSERT INTO memory (origin="graph", node_type="device"|"link"|..., ...)
```

Query path:

```
User query → Vector similarity + BM25 → Fusion ranking → Top-N snippets → LLM answer
```

---

## Exporting to Obsidian

The `.olav/knowledge/` directory is an Obsidian vault. Export the UKS back to Obsidian for browsing and editing:

```bash
olav kb export        # LanceDB → .olav/knowledge/ (markdown files with [[wiki-links]])
```

---

## Configuring Vector Embeddings

Configure the embedding model in `.olav/config/api.json`:

=== "API Mode (Recommended)"
    ```json
    "embedding": {
      "mode": "api",
      "api": {
        "model": "openai/text-embedding-3-small",
        "base_url": "https://openrouter.ai/api/v1"
      }
    }
    ```

=== "Local Mode (Offline / Air-Gapped Environments)"
    ```json
    "embedding": {
      "mode": "local"
    }
    ```
    Uses the BAAI/bge model. CPU-only, no network connection required. Suitable for security-isolated environments.

!!! note "Knowledge Base vs Agent Memory"
    Both documents and agent memories live in the same UKS `memory` table. Documents are imported explicitly via `olav kb sync/import`. Agent memories are written automatically during OLAV operation by the L1 capture middleware (operational events) and L2 pattern extractor (distilled patterns). Team-specific behaviour rules can also be primed via `olav kb import-guides`. Both are searchable via `olav_recall_memory`. See [Self-Improving Loop](../concepts/self-improving-loop.md) and [Extending Memory](extending-memory.md).

## Source-Tier Hierarchy (v0.21.1+)

Every `*.guide.yaml` carries a **`source_tier`** field that determines
how much trust AutoRecall gives the entry.  The tier maps to a
priority + retrieval weight via a fixed table — no hand-set integers
that drift across deployments.

| Tier | Priority | Retrieval Weight | Use For |
|---|---|---|---|
| `vendor` | 10 | 2.0 | Vendor/upstream docs (Cisco BGP best practices, Junos config reference) |
| `platform` | 8 | 1.67 | OLAV-bundled architectural invariants (schema-aware view guides shipped with the platform) |
| `team` | 6 | 1.33 | Org-wide policy (NetBox naming convention, AS allocation, tenant assignments) |
| `user` | 3 | 0.83 | Individual operator preference or scratch rule (R102 conversational commits land here) |

A weight-1.67 platform guide will outrank a weight-0.83 user-tier rule
when both match the same query — so an operator-spoken preference can't
accidentally override a documented architectural invariant.

### Authoring a tiered guide

```yaml
# .olav/workspace/services/guides/netbox_naming.guide.yaml
schema_version: 2
intent: netbox_naming_convention
agent: services
source_tier: team           # required for schema v2
keywords: [netbox, naming, hostname, convention]
body: |
  Device hostnames follow `<site>-<role>-<n>`.  Status defaults
  to active.  Tenant is always `acme-network-ops` for production.
```

`olav kb import-guides .olav/workspace` will pick it up and write the row
with `weight=1.33` (team tier).

### Conversational ingestion is `user` tier

R102 `memory_curator` (the "记住 — X" / "remember that X" path) writes
`source_tier: user` unconditionally.  To promote a user-tier rule to
team-tier, copy the YAML out, edit `source_tier: team`, and land it
through your normal git PR review — the platform deliberately does not
let an LLM-generated commit cross the trust boundary on its own.

## Removing a Guide

`olav kb remove` is the audited deletion path:

```bash
olav kb remove netbox_sync_defaults --reason "tenant renamed to acme-corp in Q2"
```

The command:

1. Renames the source YAML to `<intent>.guide.yaml.removed` (a
   **tombstone**).  `prime_guides_from_dir` skips tombstoned files so a
   re-prime will not resurrect.
2. Deletes the LanceDB row by id (`guide_<agent>_<intent>`).
3. Writes a `kb_audit/<iso-ts>_remove_<intent>.yaml` row containing
   `action / intent / agent / actor / timestamp / body_sha256 / reason`.

`--reason` is required (escape with `OLAV_KB_REMOVE_ALLOW_NO_REASON=1`
for batch scripts).  `--keep-yaml` deletes the LanceDB row but leaves
the YAML on disk — useful when editing + re-priming to evict the stale
cache.

## kb_audit/ — git-trackable change record {#kb-audit-changelog}

Every KB-mutating action (R102 commit, `kb remove`, NetBox CSV push)
drops one timestamped YAML row into `<workspace>/kb_audit/`.  The
directory is **git-tracked** (explicit exception in
`ownership_manifest.yaml`) so the change record survives independent of
the runtime DBs.

```yaml
# kb_audit/2026-05-15T14-29-35_remove_netbox_sync_defaults.yaml
action: remove
intent: netbox_sync_defaults
agent: services
actor: yhvh
timestamp: '2026-05-15T14:29:35+10:00'
body_sha256: 1b3ed3914eb58bb036b13c8d9bbb1edd6002bc1f3882e69dcabdfeadf81f44a6
reason: moved to git PR flow
source_tier: user
```

This is distinct from `audit.duckdb` `audit_tool_calls` (which logs
every individual tool invocation) — `kb_audit/` rows are semantic
intent records, not execution traces, and they survive `olav reset` /
DB recreation.
