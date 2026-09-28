Validates the migrated report $ARGUMENTS against Power BI (skill `validate-report`).

1. Run `pbix2html validate "$ARGUMENTS"` and read `out/<Report>.validation.md`.
2. For each `DIFF`, diagnose it using the skill's table and propose the fix in `metrics/<Report>.yaml`. Don't change `reference_sql`.
3. For each `SKIP`, tell me which reference is missing (DBQL or exported CSV) and how to get it.
4. If everything is `OK`, update the row in PLAN.md (Validate column) and list the pending visual differences awaiting the owner's sign-off.
