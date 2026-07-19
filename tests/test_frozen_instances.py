from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generate_welds import (
    DEFAULT_SOURCE_EXCEL,
    generate_weld_instance_from_source,
    instance_metrics,
    load_frozen_weld_instance,
    save_frozen_weld_instance,
)


class FrozenInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.first_welds, cls.first_meta = generate_weld_instance_from_source(
            DEFAULT_SOURCE_EXCEL, 8, 42, target_weld_count=30
        )

    def test_generation_is_deterministic(self):
        welds, metadata = generate_weld_instance_from_source(
            DEFAULT_SOURCE_EXCEL, 8, 42, target_weld_count=30
        )
        self.assertEqual(len(welds), len(self.first_welds))
        self.assertEqual(metadata["instance_hash"], self.first_meta["instance_hash"])
        self.assertEqual(
            [(w.id, w.start_point(), w.end_point()) for w in welds],
            [(w.id, w.start_point(), w.end_point()) for w in self.first_welds],
        )

    def test_solver_seed_does_not_change_instance(self):
        first = instance_metrics(self.first_meta, self.first_welds, 42)
        second = instance_metrics(self.first_meta, self.first_welds, 99)
        self.assertEqual(first["instance_hash"], second["instance_hash"])
        self.assertEqual(first["actual_weld_count"], second["actual_weld_count"])
        self.assertEqual(first["total_original_weld_length"], second["total_original_weld_length"])
        self.assertNotEqual(first["solver_seed"], second["solver_seed"])

    def test_different_instance_seeds_usually_differ(self):
        welds, metadata = generate_weld_instance_from_source(
            DEFAULT_SOURCE_EXCEL, 8, 43
        )
        self.assertTrue(welds)
        self.assertNotEqual(metadata["instance_hash"], self.first_meta["instance_hash"])

    def test_frozen_xlsx_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            xlsx = Path(directory) / "instance.xlsx"
            json_path = Path(directory) / "instance.json"
            save_frozen_weld_instance(
                self.first_welds, self.first_meta, str(xlsx), str(json_path)
            )
            loaded, metadata = load_frozen_weld_instance(str(xlsx))
            self.assertEqual(len(loaded), len(self.first_welds))
            self.assertEqual(
                [str(weld.id) for weld in loaded],
                sorted(str(weld.id) for weld in self.first_welds),
            )
            self.assertEqual(metadata["instance_hash"], self.first_meta["instance_hash"])
            self.assertEqual(
                [(w.start_point(), w.end_point()) for w in loaded],
                [
                    (w.start_point(), w.end_point())
                    for w in sorted(self.first_welds, key=lambda item: str(item.id))
                ],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
