from pathlib import Path
import tempfile
import unittest

from audit_borders import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.baseline = Path(self.temporary.name) / "before"
        self.candidate = Path(self.temporary.name) / "after"
        self.baseline.mkdir()
        self.candidate.mkdir()

    def write(self, directory, name, points, title=None):
        points = points + points[:1]
        coordinates = "\n".join(f"{x} {y}" for x, y in points)
        (directory / f"{name}.poly").write_text(f"{title or name}\n1\n{coordinates}\nEND\nEND\n")

    def test_new_overlap_is_reported(self):
        for directory in (self.baseline, self.candidate):
            self.write(directory, "A", [(0, 0), (1, 0), (1, 1), (0, 1)])
        self.write(self.baseline, "B", [(1, 0), (2, 0), (2, 1), (1, 1)])
        self.write(self.candidate, "B", [(0.999, 0), (2, 0), (2, 1), (0.999, 1)])
        result = audit(self.candidate, self.baseline)
        self.assertEqual(len(result["introduced_overlap_parts"]), 1)
        self.assertEqual(result["new_invalid_rings"], [])

    def test_existing_overlap_is_not_a_regression(self):
        for directory in (self.baseline, self.candidate):
            self.write(directory, "A", [(0, 0), (1, 0), (1, 1), (0, 1)])
            self.write(directory, "B", [(0.9, 0), (2, 0), (2, 1), (0.9, 1)])
        result = audit(self.candidate, self.baseline)
        self.assertEqual(len(result["overlaps"]), 1)
        self.assertEqual(result["introduced_overlap_parts"], [])

    def test_new_enclosed_gap_and_occupied_enclave(self):
        outer = [(0, 0), (2, 0), (2, 2), (0, 2)]
        self.write(self.baseline, "Group_A", outer)
        # A second outer ring is unnecessary: .poly holes express the gap directly.
        self.write(self.candidate, "Group_A", outer)
        path = self.candidate / "Group_A.poly"
        path.write_text(path.read_text()[:-4] + "!hole\n0.9 0.9\n1.1 0.9\n1.1 1.1\n0.9 1.1\n0.9 0.9\nEND\nEND\n")
        result = audit(self.candidate, self.baseline, groups=["Group_"])
        self.assertEqual(len(result["introduced_interior_holes"]), 1)
        enclave = [(0.9, 0.9), (1.1, 0.9), (1.1, 1.1), (0.9, 1.1)]
        for directory in (self.baseline, self.candidate):
            self.write(directory, "Enclave", enclave)
        result = audit(self.candidate, self.baseline, groups=["Group_"])
        self.assertEqual(result["introduced_interior_holes"], [])

    def test_new_invalid_ring_and_metadata(self):
        self.write(self.baseline, "A", [(0, 0), (1, 0), (1, 1), (0, 1)])
        self.write(self.candidate, "A", [(0, 0), (1, 1), (1, 0), (0, 1)], title="Renamed")
        result = audit(self.candidate, self.baseline)
        self.assertEqual(len(result["new_invalid_rings"]), 1)
        self.assertEqual(result["changed_metadata"], ["A.poly"])

    def test_island_inside_another_union_parts_hole_is_covered(self):
        outer = [(0, 0), (4, 0), (4, 4), (3, 4), (3, 1), (1, 1), (1, 4), (0, 4)]
        island = [(1.5, 1.5), (2.5, 1.5), (2.5, 2.5), (1.5, 2.5)]
        for directory in (self.baseline, self.candidate):
            self.write(directory, "Group_A", outer)
            self.write(directory, "Group_island", island)
        self.write(self.baseline, "Group_top", [(0, 3), (1, 3), (1, 4), (0, 4)])
        self.write(self.candidate, "Group_top", [(0, 3), (4, 3), (4, 4), (0, 4)])
        result = audit(self.candidate, self.baseline, groups=["Group_"])
        self.assertEqual(len(result["interior_holes"]), 1)
        self.assertEqual(result["introduced_interior_holes"], [])

    def test_long_northern_segments_use_generator_projection(self):
        # This midpoint lies on the Mercator segment, above the lon/lat segment.
        midpoint = (5, 72.59014795112851)
        self.write(self.baseline, "Group_A", [(0, 50), (10, 50), (10, 80), midpoint, (0, 60)])
        self.write(self.candidate, "Group_A", [(0, 50), (10, 50), (10, 80), (0, 60)])
        for directory in (self.baseline, self.candidate):
            self.write(directory, "Group_B", [(0, 60), midpoint, (10, 80), (10, 84), (0, 84)])
            self.write(directory, "Group_left", [(-1, 50), (.25, 50), (.25, 84), (-1, 84)])
            self.write(directory, "Group_right", [(9.75, 50), (11, 50), (11, 84), (9.75, 84)])
        result = audit(self.candidate, self.baseline, groups=["Group_"])
        self.assertEqual(result["introduced_interior_holes"], [])
        self.assertEqual(result["introduced_overlap_parts"], [])

    def test_enclosing_an_existing_exterior_void_is_not_lost_coverage(self):
        outer = [(0, 0), (4, 0), (4, 4), (3, 4), (3, 1), (1, 1), (1, 4), (0, 4)]
        for directory in (self.baseline, self.candidate):
            self.write(directory, "Group_A", outer)
        self.write(self.baseline, "Group_B", [(0, 3), (1, 3), (1, 4), (0, 4)])
        self.write(self.candidate, "Group_B", [(0, 3), (4, 3), (4, 4), (0, 4)])
        result = audit(self.candidate, self.baseline, groups=["Group_"])
        self.assertEqual(len(result["interior_holes"]), 1)
        self.assertEqual(result["introduced_interior_holes"], [])


if __name__ == "__main__":
    unittest.main()
