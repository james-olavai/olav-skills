# Documentation build and Cloudflare deployment

The maintained source is this repository's `docs/`. Production is deployed by
**Cloudflare Workers Builds connected directly to GitHub**, using the existing
Worker **olav-docs** and domain **https://docs.olavai.com/**. GitHub Actions only
validates the build and preserves an artifact; it does not publish the site.
No Cloudflare token or account ID is needed in GitHub Actions Secrets.

## Existing Worker connection migration

The existing Worker was still connected to `james-olavai/olav-docs` on
2026-11-17. A successful push/build there can therefore keep publishing the
historical platform manual. Pushing this repository cannot change that account
setting. In the **existing olav-docs Worker → Settings → Builds**, change the
Git repository/connection to:

| Setting | Value |
| --- | --- |
| GitHub repository | `james-olavai/olav-skills` |
| Production branch | `main` |
| Root directory | Repository root (`/`) |
| Build command | `python3 -m pip install -r requirements-docs.txt && python3 -B tools/build_docs.py` |
| Deploy command | `npx --yes wrangler@4.163.0 deploy --config wrangler.docs.toml --keep-vars` |

Use Python 3.12 and Node 22 (at least 22.12) in the build environment. If the
GitHub integration only has access to selected repositories, grant it access to
`olav-skills`. Keep the existing Worker and custom domain; do not create a new
application or change DNS. Save and trigger a production build. Its GitHub
check should be **Workers Builds: olav-docs** on this repository's commit.
Cloudflare build commands live in the account's Builds settings; this file does
not change them automatically.

Validate the actual public English/Chinese home and installation pages,
harness setup, offline import, search, sitemap and old URL/anchor compatibility.
A green GitHub Actions build is not evidence of deployment. A Cloudflare build
on the old repository also does not prove that the new manual is live.

The old `olav-docs` repository retains historical sources; its GitHub Actions
deploy job is disabled. Its direct Cloudflare connection must be changed as
above before it can be treated as retired from production. For rollback, use a
previous deployment/version of this same Worker in the Cloudflare dashboard
and check the public content; do not create another site or silently reconnect
the old source. Build dependencies are ranges, so independent builds are not
guaranteed byte-identical to earlier local artifacts.
