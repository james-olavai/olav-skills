# Documentation validation and publication

The maintained source is this repository's `docs/`. GitHub Actions only runs
its strict standalone builder and preserves an artifact; it does not deploy.
No Cloudflare credentials are required in GitHub Actions Secrets.

Production keeps its existing Cloudflare Git connection to
**james-olavai/olav-docs**, `main`, serving **https://docs.olavai.com/** through
the existing **olav-docs** Worker. A reviewed static build from this repository
is exported to that publication repository's **published-site/** directory.
Its Wrangler config serves that directory; historical manual source there is
not maintained. Do not change the Cloudflare connection or create another app.

Build here with a separate Python environment:

```sh
python -m pip install -r requirements-docs.txt
python -B tools/build_docs.py
```

The publication repository's README defines the export, source-commit/file-hash
manifest and review procedure. Never hand-edit generated HTML as another source.
Push that publication repository to trigger Cloudflare's automatic production
build. This repository's Actions success proves build validation only.
After publication, check its **Workers Builds: olav-docs** commit, the live
`/docs-release.json`, English/Chinese pages, installation, harness setup,
offline import, search, sitemap and old URL/anchor compatibility. Build dependency
ranges mean separate builds need not be byte-identical.
