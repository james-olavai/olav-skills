# Security Model

OLAV was designed from the ground up with security requirements for multi-user, team collaboration scenarios. This page covers how authentication, authorization, and data isolation work.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-23 | `olav admin "add-user/list-users/revoke-token"` user management | ✅ v0.10.0 |
    | C-L2-28 | Supports none/token/ldap/ad/oidc authentication modes | ✅ v0.10.0 |
    | C-L2-12 | Multi-user concurrent audit with no write conflicts | ✅ v0.10.0 |

---

!!! info "Enterprise feature (olav-ent)"
    The multi-user **authentication providers** in this section — token / server-token / LDAP, `olav admin add-user`, `olav login` and per-user tokens — ship in the enterprise extension `olav-ent` (`pip install olav-ent`). Open-source OLAV runs with `auth.mode=none`: the OS user is the identity and has full admin rights. The **RBAC engine** (roles, permission matrix, `(agent_id, skill_name, action)` checks) is part of open-source OLAV; the token/LDAP providers below are what populate it in a multi-user deployment.

## Authentication: Verifying Who You Are

Each user has a unique token stored at `~/.olav/token`. OLAV validates this token on every request.

### Adding a New User

An administrator creates the user and assigns a role:

```bash
olav admin "add-user alice --role user"
```

After execution, a one-time token is printed -- share it with the user immediately, as it cannot be retrieved again.

### User Configures Token

After receiving the token, the user saves it locally:

```bash
mkdir -p ~/.olav
echo "TOKEN_HERE" > ~/.olav/token
chmod 600 ~/.olav/token
```

Tokens are stored as SHA256 hashes (salted) in `.olav/databases/users.duckdb`. The plaintext token is never saved.

---

## Authorization: What You Can Do

OLAV uses a fixed three-tier role model (RBAC), simple and intuitive:

| Role | Can Do | Cannot Do |
|------|--------|-----------|
| `admin` | Everything -- manage users, view all logs, modify configuration, install skills | -- |
| `user` | Run queries, call tools, view own audit logs | Install/uninstall skills, manage users |
| `readonly` | Read-only queries | Any write operation (modify data, execute commands) |

The permission system matches on `(agent_id, skill_name, action)` triples, supporting fine-grained access control down to specific Agents and Skills. Four operation types:

- **use**: Run queries, call tools
- **mutate**: Modify data, execute write operations
- **install**: Install/uninstall skills
- **admin**: User management, configuration changes

---

## Multi-User Concurrency Safety

OLAV supports multiple users working simultaneously in the same project directory, with clear security boundaries for each resource:

| Resource | Storage Location | Concurrency Safe | Notes |
|----------|-----------------|-----------------|-------|
| Audit logs | `.olav/databases/audit.duckdb` | ✅ | DuckDB atomic writes, each record tagged with user_id |
| Sessions | `.olav/databases/audit.duckdb` (sessions table) | ✅ | Per-user; thread access enforced at API level |
| LLM cache | `~/.olav/cache/` | ✅ | User-isolated, no interference |
| Auth tokens | `~/.olav/token` | ✅ | Only readable by current user (chmod 600) |

**Thread ownership enforcement**: When `auth.mode=token`, the API server (`stream_run`) verifies that the requesting user owns the `thread_id`. Accessing another user's thread returns HTTP 403. Admins can access all threads.

---

## Authentication Modes

Choose the appropriate authentication method based on your team size and security requirements. Configure in `auth.mode` within `.olav/config/api.json`:

| Mode | Use Case | Description |
|------|----------|-------------|
| `none` | Personal use, local development | **Default (open-source)** — OS user is the identity and holds full admin rights, no token required |
| `token` | Small teams | **Enterprise (olav-ent)** — OLAV built-in token authentication, simplest multi-user option |
| `ldap` | Enterprise environments | **Enterprise (olav-ent)** — integrates with LDAP directory services |
| `ad` | — | Not implemented — **fails fast** with an error (no silent downgrade) |
| `oidc` | — | Not implemented — **fails fast** with an error (no silent downgrade) |

---

## Credential Security: What to Commit and What Not To

| Content | Commit to Git? | Reason |
|---------|---------------|--------|
| `.olav/workspace/` | ✅ Commit | Agent and Skill definitions, no sensitive information |
| `.olav/config/` | ❌ Add to .gitignore | Contains API keys and authentication configuration |
| `.olav/databases/` | ❌ Add to .gitignore | Contains audit logs and business data |

!!! tip "Audit Log Tamper Protection"
    OLAV's audit system generates a SHA256 digest for each log entry and writes it to an Audit Manifest file, meeting NIST AU-9 tamper protection requirements. You can use the built-in integrity verification feature to detect whether logs have been modified.

See [Users and Roles Reference ->](../reference/users-and-roles.md) for details.

---

## Sensitive Data Redaction

When OLAV collects network device output (running configs, route tables, SNMP responses) it cannot guarantee the operator hasn't told the device to print a password, an SNMP community, or a BGP MD5 key in cleartext. OLAV applies a two-layer redaction pipeline so credentials never land in the audit database or exported datasets, while leaving the topology data intact for diff and diagnostic analysis.

### Two-layer Architecture

| Layer | Implementation | Replacement Marker | When It Runs | Purpose |
|-------|---------------|--------------------|--------------|---------|
| **Collection-time** | `olav.core.redaction.scrub` (wraps [netconan](https://github.com/intentionet/netconan), ~50 KB pure Python from the Batfish team) | `netconanRemoved0`, `netconanRemoved1`, ... | At the collection boundary (`netops_init/run.py:_collect_cmd`) **and** as defense-in-depth inside `AuditEventRecorder.record_message` | Network-config-aware scrub — knows Cisco IOS / IOS-XR / NX-OS / Junos / Arista EOS syntax for passwords, SNMP communities, RADIUS / TACACS / IPSec PSK / BGP MD5 keys, etc. |
| **Enterprise gate** | `olav.core.audit_recorder.redact_sensitive` (5-pattern regex) | `[REDACTED]` | Layer 1 of `audit_dataset_export.redact_audit_run`, then re-used as a final escape detector before the dataset is written to disk | Belt-and-braces check that no plaintext credential keyword (`password`, `community`, `secret`, `key-string`, `pre-shared-key`) survives the export pipeline. |

Each layer emits a different marker on purpose — the gate layer scans for `[REDACTED]` specifically, so any escaped plaintext credential becomes a hard pipeline failure rather than silently leaking.

### What Gets Scrubbed (Collection-time)

netconan's default rule set covers the network-vendor credential surface:

- IOS `username … password 7 …` / `enable secret 5 …` hashes
- SNMP community strings (`snmp-server community netops-r0 RO`)
- BGP MD5 keys (`neighbor 10.1.12.2 password BGP_SHARED_KEY_xyz`)
- IPSec pre-shared keys
- TACACS+ / RADIUS shared secrets
- Junos `encrypted-password` (line drop) + `authentication-key` (replace)

55 built-in sensitive-item regexes + 5213 reserved-word allowlist — operators can extend either via `api.json`:

```json
{
  "redaction": {
    "enabled": true,
    "anon_ip": false,
    "extra_sensitive_words": ["community"],
    "extra_reserved_words": ["site-A-edge"]
  }
}
```

### What Is Preserved

By default (`anon_ip: false`) the scrub keeps these intact, because network diff and diagnostic logic depends on them:

- **IP addresses** (`10.1.12.2`, `192.168.10.0/24`)
- **Hostnames** (`hostname R1`)
- **ASNs** (`router bgp 65000`, `remote-as 65001`)
- **BGP neighbor relationships**
- **Interface names** (`GigabitEthernet0/1`, `ge-0/0/0`)

If you need stronger privacy (e.g. anonymising a corpus before sending to a third-party LLM provider), set `anon_ip: true` to engage the [Crypto-PAn](https://en.wikipedia.org/wiki/Crypto-PAn) prefix-preserving IP anonymizer.

### Salt Management

netconan's password rewriting and (optional) IP anonymization are keyed by a per-workspace salt:

- **Location**: `<workspace>/.redaction_salt`
- **Permissions**: `chmod 600` (auto-enforced)
- **Format**: 32 random bytes, hex-encoded (64 chars)
- **Auto-generated** on first use; stable across calls so the same input always produces the same scrubbed output (idempotent — required for cross-snapshot credential diff)
- **Fingerprint** (first 8 hex chars of `sha256(salt)`) is recorded in `Findings.salt_fingerprint` for audit cross-reference without revealing the salt itself

Different workspaces have different salts, so a scrubbed dataset from one workspace cannot be cross-correlated with another even if both contain the same passwords.

### Failure Modes — Fail-Open by Design

The redaction layer is wrapped in `try/except` so a missing dependency or pattern mismatch never blocks collection:

| Scenario | Behaviour |
|----------|-----------|
| `OLAV_REDACTION=0` | Disabled; input returned unchanged. Logged at WARNING. |
| `netconan` not installed | Logged at WARNING; input returned unchanged. Install via `pip install 'olav[redaction]'`. |
| `scrub()` raises an exception | Caught in `record_message`; raw content written to audit DB. Logged at WARNING. |
| Empty / whitespace input | Returns unchanged with `total_replacements=0`. |

If you operate in a compliance-sensitive environment, monitor the `audit_recorder` logger for `redaction skipped` warnings — repeated occurrences indicate the netconan extras are not installed where they should be.

### Installation

The redaction extras are optional to keep the base install small:

```bash
pip install 'olav[redaction]'
```

This pulls in `netconan>=0.13`. Without it, OLAV still operates — collection succeeds, but credentials reach the audit DB unredacted (Layer 2 still applies for enterprise dataset exports).

---

---

## Skill Execution Security: the execute_skill_script Mechanism

OLAV interacts directly with **production systems** — querying real network devices, reading and writing production databases, calling operational APIs. This means "execution security" has a fundamentally different threat model from a general-purpose code sandbox.

### The Wrong Defense: Container Isolation

A natural instinct is to isolate scripts inside Docker containers, but for OLAV's use case this **solves the wrong problem**:

```
Docker sandbox protects against:
  script → damages host filesystem / processes

OLAV's actual threat:
  prompt injection / LLM hallucination → wrong production operation called
```

Containers must mount database volumes and open network access to reach production systems, making the isolation wall effectively empty. More critically, even if a script runs inside a container, a production incident still happens if the LLM was tricked into calling the wrong script.

### The Right Defense: Constraining the LLM's Action Space

OLAV's `execute_skill_script` builds three layers of protection specifically for production systems:

```
┌──────────────────────────────────────────────────────┐
│  Layer 1: Path-confined execution (execute_skill_script) │
│  Execution scope enforced to <skill>/scripts/*.py;   │
│  LLM in normal operation only "sees" scripts         │
│  declared in SKILL.md (prompt-layer filter,          │
│  not execution-layer registration check)             │
├──────────────────────────────────────────────────────┤
│  Layer 2: HITL confirmation gates                     │
│  Write operations (write_profile, commit_to_memory...) │
│  require explicit user confirmation                   │
├──────────────────────────────────────────────────────┤
│  Layer 3: Immutable audit log                         │
│  Every execute_skill_script call writes to            │
│  audit_tool_calls with skill_name, script_name,       │
│  args, and timestamp                                  │
└──────────────────────────────────────────────────────┘
```

### Security Guarantees of execute_skill_script

| Dimension | deepagents LocalShellBackend | OLAV execute_skill_script |
|---|---|---|
| Execution scope | Any shell command (unrestricted) | Only `<workspace>/<skill>/scripts/*.py` (execution-enforced) |
| Script registration check | — | **Prompt layer**: SkillsMiddleware injects only `scripts:` declared entries into system prompt; no registration check at execution layer |
| Cross-agent calls | — | Allowed: workspace is a flat namespace; any agent holding this tool can call any skill |
| Argument passing | Shell string (injectable) | JSON stdin / list argv (no metacharacters) |
| Symlink escape | No detection | resolve + prefix verification |
| Timeout | No default | 120 s default, 600 s maximum |
| Audit | None | langchain hook writes `audit_tool_calls` |
| LLM interface | Path string composition (error-prone) | Semantic parameters (skill + script name) |

!!! note "Precise semantics of Layer 1"
    Layer 1 protection operates at two levels:  
    **Execution layer (enforced):** Path confinement ensures script files must reside inside `<workspace>/<skill>/scripts/` — no filesystem escape, no shell metacharacter injection.  
    **Prompt layer (semantic):** SkillsMiddleware injects only SKILL.md `scripts:`-registered entries into the system prompt, so a normally-operating LLM only "knows about" declared scripts. The execution layer itself does not additionally verify registration status — consistent with OLAV's fail-open design philosophy. All scripts in the workspace are git-managed committed code; a malicious script cannot be planted without a code commit.

For small models (such as gemma4-31b), `execute_skill_script("audit-author", "list_profiles.py", {})` has a significantly lower error rate than `execute(command="python /abs/path/to/scripts/list_profiles.py")` — the latter requires the model to correctly recall and compose a filesystem path.

### Third-Party Skill Compatibility: argv Mode

Third-party scripts written to the deepagents specification typically use `argparse` rather than JSON stdin. Declaring `argv: true` in the `scripts:` entry of `SKILL.md` causes `execute_skill_script` to automatically switch to `--key value` CLI argument mode:

```yaml
scripts:
  - name: run_report
    file: run_report.py
    argv: true          # argparse style — auto-detected and set by the installer
```

Under both modes, path confinement, list argv (no shell injection), timeout enforcement, and audit guarantees are identical.

When installing third-party skills with `olav agent install`, the installer sub-agent automatically runs compatibility analysis and patches `SKILL.md` — no manual configuration required.

!!! info "Sandbox and Execution Security"
    Authentication/authorization is only the first layer of defense. OLAV also provides a three-layer code execution sandbox, Prompt injection scanning, and dangerous command HITL approval mechanisms. See [Agent Harness ->](agent-harness.md) for details.
