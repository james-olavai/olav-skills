# Package and host tools

The package supplies deterministic scripts dispatched by `olav-skills run`.
A harness supplies execution, filesystem, conversation and permissions.
Do not assume a host file tool is virtual or persistent: inspect its actual
contract and verify the saved result. Package installation supplies no shell,
Docker, service deploy, device CLI or delegation capability. Use only tools
actually exposed in the session, within their existing authorization.
For query scripts use the bound interpreter and authorized OLAV_HOME. Do not
replace a restricted tool with a direct DB connection or arbitrary path.
