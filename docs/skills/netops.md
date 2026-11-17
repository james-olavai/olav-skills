# netops: navigation and shared comparisons

Use netops to choose the appropriate skill and reuse shared read helpers. It reads recorded data, does not connect to devices and does not create a subagent runtime. In the standalone pack, “delegate” means load the corresponding sibling skill and use an available script through [stdin JSON](../reference/scripts.md).

## Route by the result you need

| Need | Skill |
| --- | --- |
| Import offline files and assess parsing | [importer](importer.md) |
| Statistics, metadata, bounded SQL or a draft plan | [analyzer](analyzer.md) |
| BGP, OSPF or L2 relationships | [topology](topology.md) |
| Investigation with source fragments | [reporter](reporter.md) |
| Draw relationships or polish existing text | [writer](writer.md) |
| Extend parsing for an unsupported output | [learner](learner.md) |

The pack does not include a collector, simulator, memory API or workflow runner. Do not translate old references to `task(...)` into an executable tool that your host does not have. Preserved workflows also remain metadata-only in [portable exports](../portable.md).

## Shared scripts

`query_batch` exposes bounded metadata templates also available under analyzer, reporter and topology. Prefer a single matching template over loops over devices. It accepts a bound analysis root through `OLAV_HOME`, not a model-chosen database path. Use an explicit snapshot and preserve errors for unsupported filters or sources.

`diff_snapshots` compares source and target identity-key sets; it does not detect every content change. `diff_configs` compares saved CLI text for a specific device and command. `read_change_plan` reads a saved plan, and `inspect_blast_radius` evaluates removal in an observed graph. See the [change workflow](../workflows/changes.md) before interpreting these results.

`search_commands` searches a command catalog by `device`, `platform`, `keyword` and `limit`. This catalog is different from the batch operation `commands`, which reports captured command records. A catalog entry does not mean the command ran in a snapshot and never authorizes execution on a device.

## A bounded first call

With a validated analysis home selected:

```sh
printf '%s\n' '{"operation":"context"}' | /absolute/path/to/skills-venv/bin/olav-skills run netops query_batch
```

This returns capabilities/source columns, not an inventory count. Choose a matching operation from [the script reference](../reference/scripts.md), then pass the real snapshot ID. Keep returned collection-time provenance and unknown values in the final explanation. Natural language routing by a host model needs its own tests; this navigation page does not establish routing accuracy.
