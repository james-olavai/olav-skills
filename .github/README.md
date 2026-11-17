# Documentation deployment

The maintained source is this repository's `docs/`. The `docs.yml` workflow
builds it without the retired platform and deploys the existing Cloudflare
Worker **olav-docs**, which serves **https://docs.olavai.com/**. It does not
create another site or configure DNS/custom-domain routing.

Configure repository **Actions Secrets** (not source files):

- `CLOUDFLARE_API_TOKEN`: a Cloudflare deployment token scoped to the existing
  Worker's account, with Workers Scripts edit permission.
- `CLOUDFLARE_ACCOUNT_ID`: that account's ID.

Then run **Actions → Deploy Skills documentation → Run workflow → main**.
Relevant documentation pushes to `main` also trigger it. Manual runs from other
branches cannot deploy. Missing credentials fail after the build artifact has
been preserved; a successful build by itself is not a successful deployment.

Each run retains `olav-docs-<commit>` for 30 days and publishes
`/docs-release.json` containing its source commit/run ID. After deployment,
check this file, the English and Chinese home/installation pages, search, and
legacy URL/anchor compatibility at the public domain. CI dependency versions
are ranges; a new build is not guaranteed byte-identical to a prior local build.

The old `james-olavai/olav-docs` repository retains historical sources only. Its
old deployment workflow must remain disabled to avoid overwriting this manual.
For rollback, use the previous deployment/version of the same Worker in the
Cloudflare dashboard. Do not reactivate the retired source pipeline or rebuild
another application. Confirm public routes and `/docs-release.json` after any
rollback. Deployment history and domain binding require account access to verify.
