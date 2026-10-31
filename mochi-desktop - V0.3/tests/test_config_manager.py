import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import config_manager


class AtomicConfigWriteTests(unittest.TestCase):
    def test_serialization_failure_preserves_the_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            original = {"brain": {"enabled": True, "model": "original"}}
            config_path.write_text(json.dumps(original), encoding="utf-8")

            with (
                patch.object(config_manager, "CONFIG_DIR", Path(directory)),
                patch.object(config_manager, "CONFIG_PATH", config_path),
            ):
                result = config_manager.save_config({"invalid": object()})

            self.assertFalse(result)
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                original,
            )
            self.assertEqual(list(Path(directory).glob(".config-*.tmp")), [])

    def test_concurrent_writers_never_publish_partial_json(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"

            def write_config(index):
                payload = {
                    "writer": index,
                    "data": [index] * 250,
                }
                self.assertTrue(config_manager.save_config(payload))

            with (
                patch.object(config_manager, "CONFIG_DIR", Path(directory)),
                patch.object(config_manager, "CONFIG_PATH", config_path),
            ):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(write_config, range(64)))

                final = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertIn(final["writer"], range(64))
            self.assertEqual(final["data"], [final["writer"]] * 250)
            self.assertEqual(list(Path(directory).glob(".config-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
