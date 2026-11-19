# Optional topology intent

A project may record intended protocols as a YAML `protocols` list (bgp, ospf,
cdp_lldp, bfd, hsrp, isis, ldp, vrrp). This is descriptive configuration, not
permission or an automatic discovery trigger in the standalone package.

Built-in queries are bgp/ospf/l2/all. For another concept explicitly check
list_recipes, then use the opt-in prepare_recipe → draft → save_recipe →
rebuild_views workflow in SKILL.md. No slash command or model provider is
supplied. Use actual sample columns and exact supported vendor hints; verify
row counts/skipped results. Query custom views through bounded read-only SQL.
