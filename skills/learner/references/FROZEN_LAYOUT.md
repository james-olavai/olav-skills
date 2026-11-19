# Frozen parser persistence

finish_learn validates samples and freezes under the authorized project's
`.olav/templates/`:

- `<platform>/<command_safe>.textfsm`: custom TextFSM.
- `parsers/<platform>/<command_safe>.py`: Python with sufficient samples.
- `parsers/_quarantine/<platform>/<command_safe>.py`: low-sample Python.

The returned path and quarantined flag are authoritative. TextFSM is live even
with one sample; report that overfit risk. Quarantined Python is excluded from
normal parser lookup. Passing more samples is a separate explicit learning
operation, not an automatic promotion service.

Headers record platform, command, learned time, samples hash and contract version.
The samples hash covers concatenated raw samples in supplied order; do not
interpret it as sorted-set identity. Normal lookup prefers main Python, then
custom TextFSM, then bundled ntc-templates. Existing parsed DB rows are not
reprocessed just because a new parser exists. No standalone failure-cache CLI,
force-relearn flag, scheduled retries or batch-learning service is supplied.
