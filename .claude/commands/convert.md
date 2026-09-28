Converts a Power BI report to HTML following the project pipeline. Argument: path to the .pbix ($ARGUMENTS).

Steps:
1. Run `pbix2html extract "$ARGUMENTS" --out out` and read `out/<Report>/layout.json` and `model.json`. Summarize: pages, visuals by type, custom visuals, slicers, measures, RLS rules, storage mode.
2. If `metrics/<Report>.yaml` doesn't exist, generate the scaffold with `pbix2html scaffold "$ARGUMENTS"` (uses `metrics/_template.yaml`). For each visual with data, propose the Teradata `sql` from the DAX measures (skill `dax-to-teradata-sql`) and leave `reference_sql` empty so the DBQL SQL can be pasted in. Mark with `TODO` whatever you can't infer.
3. Show me the yaml and wait for my review before running anything against Teradata.
4. With the yaml approved, run `pbix2html convert "$ARGUMENTS" --mode snapshot` (add `--role` if the report has RLS) and open/describe the resulting HTML.
5. Run `/validate <Report>` and update the report's row in PLAN.md.

Don't migrate more than one report per invocation. If the .pbix is PBIR/PBIP format, stop and say so.
