#!/usr/bin/env python3
"""Audit .poly geometry and compare a candidate directory with its sources.

Requires NumPy and Shapely. Geometry repairs are used only for measurements;
this tool never writes polygons. Checks use the generator's degrees-scaled
Mercator segments. Areas are corrected for latitude, suitable for finding
slivers, not surveying; reported bounds and points are longitude/latitude.
"""

import argparse
import json
import logging
import math
from pathlib import Path

import numpy as np
from shapely import STRtree, make_valid, prepare
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from maintain_borders import border_geometry, prepare_validity, project, read_poly


LOG = logging.getLogger(__name__)


def area_m2(geometry):
    if geometry.is_empty or not geometry.area:
        return 0.0
    latitude = math.atan(math.sinh(math.radians(geometry.centroid.y)))
    return geometry.area * 111320**2 * math.cos(latitude)**2


def geographic_bounds(geometry):
    left, bottom, right, top = geometry.bounds
    return (left, math.degrees(math.atan(math.sinh(math.radians(bottom)))),
            right, math.degrees(math.atan(math.sinh(math.radians(top)))))


def geographic_point(geometry):
    point = geometry.representative_point()
    return Point(point.x, math.degrees(math.atan(math.sinh(math.radians(point.y))))).wkt


def load(directory):
    borders = [read_poly(path) for path in sorted(directory.glob("*.poly"))]
    if not borders:
        raise ValueError(f"No .poly files in {directory}")
    invalid_rings, invalid_files = prepare_validity(borders)
    shapes = []
    for border in borders:
        geometry = border_geometry(border, [project(ring.points) for ring in border.rings])
        if geometry is None:
            raise ValueError(f"Cannot assign holes in {border.name}")
        shapes.append(geometry if geometry.is_valid else make_valid(geometry))
    duplicate_records = 0
    unclosed = []
    for border in borders:
        for ring in border.rings:
            duplicate_records += int(np.count_nonzero(np.all(ring.points[1:] == ring.points[:-1], axis=1)))
            if not np.array_equal(ring.points[0], ring.points[-1]):
                unclosed.append({"file": border.name, "ring": ring.name})
    return borders, shapes, {
        "coordinate_system": "Degrees-scaled spherical Mercator, matching generator LoadBorders",
        "files": len(borders),
        "coordinate_records": sum(len(ring.points) for border in borders for ring in border.rings),
        "invalid_rings": invalid_rings,
        "invalid_files": invalid_files,
        "consecutive_duplicate_records": duplicate_records,
        "unclosed_rings": unclosed,
    }


def candidate_pairs(shapes):
    tree = STRtree(shapes)
    return {(i, int(j)) for i, shape in enumerate(shapes) for j in tree.query(shape) if j > i}


def interior_holes(shapes):
    union = unary_union(shapes)
    parts = union.geoms if hasattr(union, "geoms") else [union]
    return [Polygon(ring) for part in parts if isinstance(part, Polygon) for ring in part.interiors]


def audit(directory, baseline=None, groups=(), minimum_area=1.0):
    borders, shapes, report = load(directory)
    names = [border.name for border in borders]
    pairs = candidate_pairs(shapes)
    old_shapes = None
    if baseline:
        old_borders, old_shapes, old_report = load(baseline)
        if names != [border.name for border in old_borders]:
            raise ValueError("Candidate and baseline have different .poly file names")
        pairs |= candidate_pairs(old_shapes)
        report["baseline"] = old_report
        old_invalid = {(item["file"], item["ring"]) for item in old_report["invalid_rings"]}
        report["new_invalid_rings"] = [
            item for item in report["invalid_rings"] if (item["file"], item["ring"]) not in old_invalid
        ]
        report["new_invalid_files"] = sorted(set(report["invalid_files"]) - set(old_report["invalid_files"]))
        report["changed_metadata"] = [
            new.name for new, old in zip(borders, old_borders)
            if new.title != old.title or [ring.name for ring in new.rings] != [ring.name for ring in old.rings]
        ]
    overlaps = []
    introduced = []
    for number, (i, j) in enumerate(sorted(pairs)):
        intersection = shapes[i].intersection(shapes[j])
        area = area_m2(intersection)
        if area > minimum_area:
            overlaps.append({"a": names[i], "b": names[j], "approx_m2": area,
                             "bounds": geographic_bounds(intersection)})
        if old_shapes is not None and area > minimum_area:
            old_intersection = old_shapes[i].intersection(old_shapes[j])
            new_part = intersection.difference(old_intersection)
            added_area = area_m2(new_part)
            if added_area > minimum_area:
                introduced.append({"a": names[i], "b": names[j], "approx_m2": added_area,
                                   "bounds": geographic_bounds(new_part)})
        if number % 500 == 0:
            LOG.info("Checked %s/%s neighboring pairs", number, len(pairs))
    report["candidate_pairs"] = len(pairs)
    report["overlaps"] = sorted(overlaps, key=lambda item: -item["approx_m2"])
    if baseline:
        report["introduced_overlap_parts"] = sorted(introduced, key=lambda item: -item["approx_m2"])
    holes = []
    new_holes = []
    global_tree = STRtree(shapes)
    for group in groups:
        selected = [i for i, name in enumerate(names) if group == "*" or name.startswith(group)]
        members = [shapes[i] for i in selected]
        if not members:
            raise ValueError(f"No files match group {group}")
        old_coverage = None
        if old_shapes is not None:
            old_coverage = unary_union([old_shapes[i] for i in selected])
            prepare(old_coverage)
        for hole in interior_holes(members):
            area = area_m2(hole)
            if area > minimum_area:
                holes.append({"group": group, "approx_m2": area, "bounds": geographic_bounds(hole),
                              "point": geographic_point(hole)})
                if old_coverage is not None:
                    # An old exterior void can become enclosed when coverage
                    # grows. Only formerly covered parts are newly uncovered.
                    if not old_coverage.intersects(hole) or old_coverage.touches(hole):
                        continue
                    added = hole.intersection(old_coverage)
                    if area_m2(added) > minimum_area:
                        # Other maps can occupy an enclave omitted from this filename group.
                        occupants = [shapes[i] for i in global_tree.query(added)]
                        added = added.difference(unary_union(occupants))
                        if area_m2(added) > minimum_area:
                            neighbors = [names[i] for i in global_tree.query(added)
                                         if shapes[i].distance(added) < 1e-9]
                            new_holes.append({"group": group, "approx_m2": area_m2(added),
                                              "bounds": geographic_bounds(added), "neighbors": neighbors})
        LOG.info("Checked interior holes for %s", group)
    report["interior_holes"] = sorted(holes, key=lambda item: -item["approx_m2"])
    if baseline:
        report["introduced_interior_holes"] = sorted(new_holes, key=lambda item: -item["approx_m2"])
    report["minimum_area_m2"] = minimum_area
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--group", action="append", default=[], help="Filename prefix for union hole checks; '*' for all")
    parser.add_argument("--minimum-area", type=float, default=1.0, help="Report areas above this many square meters")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.minimum_area) or args.minimum_area < 0:
        parser.error("Minimum area must be finite and nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    report = audit(args.directory, args.baseline, args.group, args.minimum_area)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    LOG.info("%s files, %s invalid rings, %s overlapping pairs; report: %s",
             report["files"], len(report["invalid_rings"]), len(report["overlaps"]), args.report)
    if args.baseline and (report["new_invalid_rings"] or report["new_invalid_files"] or report["changed_metadata"]
                          or report["introduced_overlap_parts"] or report["introduced_interior_holes"]
                          or report["unclosed_rings"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
