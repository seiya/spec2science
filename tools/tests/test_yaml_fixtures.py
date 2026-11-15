"""`load_yaml_fixture`: what a row gets is a fresh parse's value, whatever an earlier row did."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from tools.tests.yaml_fixtures import load_yaml_fixture


class LoadYamlFixtureTest(unittest.TestCase):
    def test_an_edit_of_what_one_call_returned_reaches_no_later_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir.yaml"
            path.write_text("a: &x [1, 2]\nb: *x\nc: {d: .nan}\n")
            first = load_yaml_fixture(path)
            first["a"].append(3)
            first["c"]["d"] = 0
            del first["b"]
            second = load_yaml_fixture(path)
            self.assertEqual(second["a"], [1, 2])
            self.assertIs(second["a"], second["b"])   # an alias stays one object, as in a parse
            self.assertEqual(repr(second), repr(yaml.safe_load(path.read_text())))

    def test_an_edited_file_is_parsed_afresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir.yaml"
            path.write_text("a: 1\n")
            self.assertEqual(load_yaml_fixture(path), {"a": 1})
            path.write_text("a: 2\n")   # same path, same length
            self.assertEqual(load_yaml_fixture(path), {"a": 2})


if __name__ == "__main__":
    unittest.main()
