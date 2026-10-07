# Manually maintain source borders

The `.poly` files in `data/borders` partition map generation. They include
administrative borders, artificial download divisions and offshore coverage.
Keep filenames, coverage and shared junctions when updating them. A changed
boundary must use the same line in every affected neighbour.

These tools work offline. They do not fetch current OpenStreetMap boundaries.
They read every `.poly` in an input directory and write candidates separately.
Use the complete border set for transformations so neighbouring owners are
available. Output directories must be empty or absent, and must not contain or
be contained by their input directory.

## Set up a working directory

Run the commands from the repository root in the same shell. Python 3.10 or
later is required. `requirements.txt` installs NumPy 1.23+ and Shapely 2.0+.
Alignment also requires GEOS 3.10+; standard Shapely wheels include GEOS.

```sh
border_work=$(mktemp -d /tmp/om-borders.XXXXXX)
python3 -m venv "$border_work/venv"
border_python="$border_work/venv/bin/python"
"$border_python" -m pip install -r tools/python/borders/requirements.txt

mkdir "$border_work/00-baseline"
cp data/borders/*.poly "$border_work/00-baseline/"
```

Keep this baseline unchanged through every pass. The other child directories
below are distinct outputs; the tools create them. A new run should use a new
working directory.

## Tools and arguments

Positional arguments come first in these examples. All tools accept `--help`.

| Tool | Arguments and purpose | Output report |
| --- | --- | --- |
| `repair_backtracks.py` | `INPUT OUTPUT`: cancel exact zero-area retraces. | `backtrack-repair-report.json` |
| `align_border_points.py` | `INPUT OUTPUT`: insert nearby existing source points into unmatched edges. | `junction-report.json` |
| `reconcile_borders.py` | `INPUT OUTPUT`: reuse one existing path for nearly identical shared arcs. | `normalization-report.json` |
| `maintain_borders.py` | `INPUT OUTPUT`: Douglas–Peucker simplification of canonical shared arcs. | `report.json` |
| `audit_borders.py` | `DIRECTORY --report JSON`: inspect geometry; add `--baseline DIRECTORY` to compare. | The requested JSON path |

Alignment, reconciliation and simplification accept `--epsilon EPSILON` and
repeatable `--preserve-file NAME`. Names are input filenames, with or without
`.poly`; unknown names fail. Preservation excludes alignment donors and targets,
locks reconciliation groups involving the file, and protects its simplification
arcs in all owners. Backtrack cleanup has no epsilon or preservation flag.

Alignment additionally accepts repeatable `--allow-invalid-file NAME` and
`--exclude-edges JSON`. The invalid-file exception is limited to single-ring
files: replacements must introduce no new local intersections and retain the
existing validity reason. Use it only after inspecting the affected geometry,
then compare the complete candidate against the original baseline.

Audit accepts repeatable `--group PREFIX` and `--minimum-area SQUARE_METERS`.
`--report` is required. Groups affect enclosed-hole union checks only; validity,
metadata and neighbour overlap checks still cover the whole directory.

## Run the required passes once

Choose audit groups covering the changed areas. The commands below illustrate
France and Germany; extend the groups for other affected regions. A prefix such
as `France_` selects its download regions; `Andorra` selects a single-file
country. These groups do not limit which files the transformations process.

```sh
"$border_python" tools/python/borders/audit_borders.py "$border_work/00-baseline" \
  --group France_ --group Germany_ --minimum-area 0.01 \
  --report "$border_work/before.json"

"$border_python" tools/python/borders/repair_backtracks.py \
  "$border_work/00-baseline" "$border_work/01-backtracks"

"$border_python" tools/python/borders/align_border_points.py \
  "$border_work/01-backtracks" "$border_work/02-aligned" --epsilon 1e-5

"$border_python" tools/python/borders/reconcile_borders.py \
  "$border_work/02-aligned" "$border_work/03-matched" --epsilon 1e-5

"$border_python" tools/python/borders/maintain_borders.py \
  "$border_work/03-matched" "$border_work/04-candidate" --epsilon 1e-5

"$border_python" tools/python/borders/audit_borders.py "$border_work/04-candidate" \
  --baseline "$border_work/00-baseline" \
  --group France_ --group Germany_ --minimum-area 0.01 \
  --report "$border_work/after.json"

"$border_python" -m unittest discover -s tools/python/borders -p 'test_*.py'
```

Skip unnecessary passes by giving the next tool the preceding output or the
baseline directly. Backtrack cleanup comes first because it can make an invalid
ring eligible for later passes without moving its boundary. Do not repeatedly
simplify generated outputs: each run can add another epsilon of displacement.
Review each stage's report, especially rollbacks and unresolved matches.

Backtrack cleanup cancels `A-B-A`, or horizontal/vertical partial reversals with
`B` outside `A-C`. These lines remain collinear in Mercator. Every changed file
must retain exactly the same projected filled geometry, preserving its areal
intersection with every neighbour. The writer retains original coordinate text,
metadata and blank lines; a new closing record copies existing source text.
Sloped partial longitude/latitude retraces and other crossings remain for source review.

Alignment never averages coordinates. It inserts exact points already shared
by regions, or points whose donor ring also contains both target edge endpoints.
Ambiguous matches and points near endpoints are excluded. Reconciliation matches
paths between exact common endpoints and retains the path with more distinct
vertices, within a continuous distance bound. Density does not prove accuracy
against current OpenStreetMap. Invalid alignment targets and reconciliation
groups touching invalid files are excluded by default; invalid files can still
donate alignment points unless preserved. Invalidating replacements are rolled
back.

## Optional reviewed edge exclusions

Create your own JSON file when an alignment insertion causes a regression.
Use an actual input filename and the two exact original longitude/latitude
endpoints of the edge, for example by copying them from `junction-report.json`.
The format is a JSON array; replace the illustrative values below:

```json
[
  {
    "file": "Region.poly",
    "endpoints": [[12.345, 45.678], [12.346, 45.679]],
    "reason": "Reviewed insertion would change neighbouring coverage."
  }
]
```

Save it as `$border_work/excluded-edges.json`, then add
`--exclude-edges "$border_work/excluded-edges.json"` to alignment. Use a fresh
output directory when rerunning that pass and all subsequent passes. Excluded
edges still count when rejecting ambiguous matches. Absent historical edges
are reported as unmatched exclusions; unknown filenames fail. No repository
exclusion file is required.

## Precision and validation

The default epsilon is **1e-5 in Organic Maps' degrees-scaled spherical
Mercator coordinates**, about 1.11 m at the equator multiplied by
`cos(latitude)` on the ground. It matches `kMwmPointAccuracy`; the historical
source simplification tolerance is undocumented. Alignment, reconciliation and
DP each have this bound, so their combined displacement can be up to `3e-5`.
Source-backed administrative updates are separate changes and need their own
coverage and junction proofs.

C++ `GeneratePackedBorders()` uses display tolerances
`360 * 1.3 / (256 * 2^zoom)`: 0.0017852783203125 at zoom 10 and
0.00714111328125 at zoom 8. Those are not source maintenance tolerances. The
packed-border simplifier uses near-optimal dynamic programming; the Python
simplifier uses Douglas–Peucker.

The simplifier interns exact vertices and undirected edges across all files.
Branches and changes in edge ownership fix junctions. Each common arc is
simplified once and reused in every owner; closed shared rings also normalize
rotation and direction. Invalid source rings protect their vertices in every
neighbour. Unmatched edges connected to shared topology are preserved. If a
valid ring or file would become invalid, its arcs are locked in every owner. This preserves
already matching shared lines; it does not repair all mismatched input borders.

Geometry checks use projected vertices and straight Mercator segments, matching
map generation. Straight longitude/latitude segments can differ on long edges.
Reports convert locations back to longitude/latitude; square-metre areas use an
approximate squared-cosine latitude correction. Pre-existing invalid geometry
is repaired temporarily for measurements only, never written as a repair.

With `--baseline`, audit exits nonzero for newly invalid rings/files, changed
metadata, new overlap or enclosed uncovered parts above the area threshold,
or any unclosed candidate ring. The default area threshold is 1 m²; the example
uses 0.01 m². Existing invalid rings, overlaps and holes are reported even when
comparison succeeds. Some overlaps and enclaves are intentional download
coverage, so inspect the source before changing them.

The baseline hole comparison flags only enclosed parts that were covered in
the baseline and are now uncovered by every candidate region. They miss gaps connected to the
selected union's exterior. Separate country checks can therefore miss an
international junction gap: review the shared lines and use a union containing
all relevant neighbours. `--group '*'` selects all files, but can be expensive
and does not unwrap the antimeridian. A clean example-country audit is not a
proof of globally gap-free coverage. Wider discrepancies and current boundary
updates require verified OpenStreetMap or other authoritative source geometry,
applied to every neighbour while retaining curated download divisions.

## Apply a reviewed candidate

Apply only after the audit succeeds, the tests pass, and manual review covers
the changed boundaries and relevant junctions. Inspect the JSON reports and the
candidate `.poly` changes. If source files changed since the snapshot, start
again from the current source and revalidate.

The equality check below prevents copying over a changed source directory.
Only `.poly` files are copied; stage reports stay in the working directory.

```sh
diff -qr "$border_work/00-baseline" data/borders &&
  cp "$border_work/04-candidate/"*.poly data/borders/

git diff --stat -- data/borders
git diff -- data/borders
```

If you skipped passes or chose different output names, use your final validated
candidate directory in the copy command.
