# Ask network questions in Open WebUI

Use this guide if your organization has configured the Olav integration in Open WebUI. The integration answers from recorded network data in the tenant you can access. The administrator controls which tenant entries, read-only capabilities and downloads are available to your account.

## Try the public demo

The [Olav Demo](https://olavai.com/demo/) uses synthetic network data in two
separate tenants. Use the account and tenant entry assigned to you. Queries and
download links are scoped to that account's authorized tenant. Follow the
instructions on the Demo page to request access.

## Start a chat

1. Open the Open WebUI address supplied by your administrator and sign in with your assigned account.
2. In the model picker, select the entry your administrator assigned to the tenant you want to inspect.
3. Ask in your own words. When a reply includes a file, open its link while signed in with the account that requested it.

The conversations below are fictional and the counts are illustrative. The English exchanges are translations for this guide.

## Count devices and check role coverage

> **You** — How many devices do we have now?
>
> **Reply** — The current inventory for this tenant contains 12 recorded devices. This count comes from the inventory on file. Their live status is unknown.

> **You** — How many core switches do we have?
>
> **Reply** — The total is unknown. In the 5 records I could review, 0 are confirmed core switches, 2 can be ruled out and 3 lack enough information to classify.

An inventory count describes the records on file. A role count also needs evidence of device class and role. The second answer covers one page of records, so the counts from that page cannot establish a tenant-wide total.

## Download the complete current inventory

> **You** — Can you make the device list into a report I can download?
>
> **Reply** — The Markdown report contains all 12 devices in this tenant’s current inventory. Open the report link to download it.

When the reply confirms a complete report, use the **Markdown report** for the full current inventory in that tenant. The report lists device name, management address, platform, vendor, model and role, with unrecorded values shown as missing.

The separate `returned-results.csv` file, when offered, is an audit of the records returned by the queries made during the conversation turn. Its coverage ends at those returned records.

## Read the scope of a topology diagram

> **You** — Can you draw me a topology diagram?
>
> **Reply** — I drew 2 of the 8 recorded Layer 2 adjacencies in the source. The diagram and the accompanying report use the same returned records.

Open the diagram and its report from the reply when both are available. Read the source and coverage in the report before using the diagram. Its edges describe the recorded adjacencies included in the result. Whole-network coverage and live connectivity remain unverified.

## When the scope is unclear or the evidence is missing

| You ask | Illustrative reply |
| --- | --- |
| “Can you make a list of the important ones?” | “Which devices do you mean? Please give me their names or tell me how to identify them.” |
| “How many devices did we have last month?” | “I don’t have evidence for last month’s inventory. The historical count is unknown.” |
| “Only show me devices with a management address.” | “The available records don’t establish which devices meet that condition. I can’t verify a complete list for that filter.” |
| “Just send me the branch office list.” | “The available records don’t identify which devices belong to the branch office. I can’t verify that list.” |

If the reply asks for clarification, specify the devices, filter or location you mean. Historical counts and filtered lists still need evidence for the requested time, field or site. When a list’s scope cannot be established, the reply asks for clarification or explains the gap without listing devices or offering a download link for that request.

## Use the standalone package locally

The public `olav-skills` Python package provides the CLI, skill resources and documented bindings for a separately configured AI harness. Open WebUI login, tenant selection and browser downloads come from the privately configured integration. For local use, start with the [standalone installation guide](installation.md).
