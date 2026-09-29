import pandas as pd, os, sys
sys.stdout.reconfigure(encoding="utf-8")  

RAW = "raw"    
# files to run the check on, in the order they should be processed (parents before children)
files = ["themes", "colors", "part_categories", "parts",
         "sets", "inventories", "inventory_parts", "inventory_sets"]

df = {f: pd.read_csv(f"{RAW}/{f}.csv", dtype=str, keep_default_na=False)
      for f in files}   # read everything as text so nothing is silently altered

# primary keys per table
pk = {"themes": ["id"], "colors": ["id"], "part_categories": ["id"],
      "parts": ["part_num"], "sets": ["set_num"], "inventories": ["id"],
      "inventory_parts": ["inventory_id", "part_num", "color_id", "is_spare"],
      "inventory_sets": ["inventory_id", "set_num"]}

# foreign keys: (child_table, child_col, parent_table, parent_col)
fks = [("sets", "theme_id", "themes", "id"),
       ("themes", "parent_id", "themes", "id"),
       ("parts", "part_cat_id", "part_categories", "id"),
       ("inventories", "set_num", "sets", "set_num"),
       ("inventory_parts", "inventory_id", "inventories", "id"),
       ("inventory_parts", "part_num", "parts", "part_num"),
       ("inventory_parts", "color_id", "colors", "id"),
       ("inventory_sets", "inventory_id", "inventories", "id"),
       ("inventory_sets", "set_num", "sets", "set_num")]

# group foreign keys by child table, so they print under the child's block
fks_by_child = {}
for c, ccol, p, pcol in fks:
    fks_by_child.setdefault(c, []).append((ccol, p, pcol))

overall_needs_cleaning = []

MAX_EXAMPLES = 15   # example rows print per issue, to keep output readable

def show_examples(rows, key_cols, issue_col, note="", show_repr=False):
    """Print up to MAX_EXAMPLES rows identified by their primary key, for a flagged issue."""
    n = len(rows)
    shown = rows.head(MAX_EXAMPLES)
    for _, r in shown.iterrows():
        key = ", ".join(f"{c}={r[c]}" for c in key_cols)
        val = repr(r[issue_col]) if show_repr else r[issue_col]
        tag = f" ({note})" if note else ""
        print(f"       [{key}] {issue_col}={val}{tag}")
    if n > MAX_EXAMPLES:
        print(f"       ... and {n - MAX_EXAMPLES} more rows")

for f in files:
    d = df[f]
    print(f"=== {f}.csv ===")
    print(f"  rows: {len(d):,} | columns: {list(d.columns)}")

    mb = os.path.getsize(f"{RAW}/{f}.csv") / 1e6
    print(f"  file size: {mb:.2f} MB")

    # missing values (blank string counts as missing)
    blanks = (d == "").sum()
    blanks = blanks[blanks > 0]
    if len(blanks) > 0:
        for col, cnt in blanks.items():
            print(f"  [ISSUE] missing values in '{col}': {cnt}")
            rows = d[d[col] == ""]
            show_examples(rows, pk[f], col, note="blank")
        overall_needs_cleaning.append(f)
    else:
        print("  [OK] no missing values")

    # whole-row duplicates
    dup_rows = d.duplicated().sum()
    if dup_rows > 0:
        print(f"  [ISSUE] duplicate rows: {dup_rows}")
        overall_needs_cleaning.append(f)
    else:
        print("  [OK] no duplicate rows")

    # duplicate primary key
    dup_pk = d.duplicated(subset=pk[f]).sum()
    if dup_pk > 0:
        print(f"  [ISSUE] duplicate primary key ({pk[f]}): {dup_pk}")
        overall_needs_cleaning.append(f)
    else:
        print(f"  [OK] no duplicate primary key ({pk[f]})")

    # whitespace issues in any text column
    ws_total = 0
    for col in d.columns:
        mask = d[col] != d[col].str.strip()
        n = mask.sum()
        if n > 0:
            print(f"  [ISSUE] stray whitespace in '{col}': {n}")
            show_examples(d[mask], pk[f], col, note="whitespace", show_repr=True)
            ws_total += n
    if ws_total == 0:
        print("  [OK] no whitespace issues")
    else:
        overall_needs_cleaning.append(f)

    # foreign key / orphan checks for this table (as the CHILD)
    if f in fks_by_child:
        for ccol, p, pcol in fks_by_child[f]:
            child_vals = d[ccol]
            child_vals = child_vals[child_vals != ""]   # blank = NULL, not an orphan
            bad = ~child_vals.isin(df[p][pcol])
            n = bad.sum()
            if n > 0:
                print(f"  [ISSUE] orphans on {ccol} -> {p}.{pcol}: {n}")
                if f == "inventory_parts" and ccol == "part_num":
                    missing_vals = child_vals[bad]
                    print(f"    -> {missing_vals.nunique()} distinct missing {ccol} values:")
                    for val, cnt in missing_vals.value_counts().items():
                        print(f"       {val}: {cnt} rows")
                overall_needs_cleaning.append(f)
            else:
                print(f"  [OK] no orphans on {ccol} -> {p}.{pcol}")

    # table-specific value checks
    if f == "sets":
        yr = pd.to_numeric(d["year"], errors="coerce")
        print(f"  year range: {yr.min()} - {yr.max()} | non-numeric: {yr.isna().sum()}")
        zero_parts = (pd.to_numeric(d["num_parts"], errors="coerce") == 0).sum()
        print(f"  sets with num_parts = 0: {zero_parts} (not an error, just a mismatch)")

    if f == "inventory_parts":
        qty = pd.to_numeric(d["quantity"])
        print(f"  quantity <= 0: {(qty <= 0).sum()}")
        print(f"  is_spare values: {sorted(d['is_spare'].unique())}")

    if f == "colors":
        print(f"  is_trans values: {sorted(d['is_trans'].unique())}")
        bad_rgb = (~d["rgb"].str.fullmatch(r"[0-9A-Fa-f]{6}")).sum()
        print(f"  bad rgb codes: {bad_rgb}")

    print()  # blank line between table blocks

# cross-table consistency check: sets.num_parts vs. summed inventory_parts
print("=== cross-table check: sets.num_parts vs. actual inventory ===")
ip = df["inventory_parts"].copy()
ip["quantity"] = pd.to_numeric(ip["quantity"])
inv = df["inventories"]
latest = (inv.assign(version=pd.to_numeric(inv["version"]))
             .sort_values("version").groupby("set_num").tail(1))
tot = ip[ip["is_spare"] == "f"].groupby("inventory_id")["quantity"].sum()
chk = latest.assign(actual=latest["id"].map(tot)).merge(df["sets"], on="set_num")
mismatch = (pd.to_numeric(chk["num_parts"]) != chk["actual"].fillna(0)).sum()
print(f"  sets where num_parts != summed inventory: {mismatch} (not an error, just a mismatch)\n")

# final summary: which files need cleaning
print("=== SUMMARY: files needing cleanup ===")
needing = sorted(set(overall_needs_cleaning))
if needing:
    for f in needing:
        print(f"  [NEEDS CLEANUP] {f}.csv")
else:
    print("  [ALL CLEAN] no files need cleanup")