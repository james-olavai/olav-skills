# Recorded routing investigation

There is no automatic memory retrieval or injected guide service. Read the
packaged references relevant to the question:

- [BGP states and path selection](../../netops/references/bgp_path_and_state_reference.guide.yaml)
- [Layered troubleshooting](troubleshoot_layered_l1_to_l4.guide.yaml)
- [Topology SQL patterns](topology_query.guide.yaml)

A protocol state narrows hypotheses; it does not establish a root cause.
Check link/interface, route to peer and transport evidence before protocol
policy. Compare both endpoints when recorded data permits. Missing capture,
unknown source time and absent views remain gaps. Graph connectivity does not
prove route selection, forwarding reachability, ECMP or convergence duration.
The package supplies no Batfish, live ping or model-backed routing solver.
