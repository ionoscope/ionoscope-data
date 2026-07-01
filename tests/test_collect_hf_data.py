import argparse
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import collect_hf_data as collector


def assert_payload_shape(test_case, payload, dataset, count):
    test_case.assertEqual(payload["schema_version"], "1.0")
    test_case.assertEqual(payload["dataset"], dataset)
    test_case.assertEqual(payload["record_count"], count)
    test_case.assertIsInstance(payload["records"], list)
    test_case.assertEqual(len(payload["records"]), count)


class JsonOutputTests(unittest.TestCase):
    def test_make_records_payload_has_uniform_shape(self):
        payload = collector.make_records_payload("sample", [{"time_utc": "2026-01-01T00:00:00Z"}])
        assert_payload_shape(self, payload, "sample", 1)

    def test_read_json_bytes_accepts_swpc_trailing_nul_bytes(self):
        payload = collector.read_json_bytes(b'[["time_tag","speed"],["2026-01-01 00:00:00.000","400"]]\x00\x00')
        self.assertEqual(payload[0], ["time_tag", "speed"])


class ParserTests(unittest.TestCase):
    def test_collect_noaa_normalizes_table_json(self):
        config = {
            "noaa": {
                "enabled": True,
                "endpoints": [{"name": "solar_wind_plasma_2_hour", "url": "https://example.test/plasma.json"}],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = collector.Paths(
                root=Path(tmp),
                raw_noaa=Path(tmp) / "raw" / "noaa",
                raw_giro=Path(tmp) / "raw" / "giro",
                processed=Path(tmp) / "processed",
            )
            for path in (paths.raw_noaa, paths.raw_giro, paths.processed):
                path.mkdir(parents=True, exist_ok=True)
            raw = b'[["time_tag","density","speed"],["2026-01-01 00:00:00.000","5.1","410.2"]]'
            with patch.object(collector, "fetch_url", return_value=raw):
                rows = collector.collect_noaa(config, paths, timeout=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "NOAA_SWPC")
        self.assertEqual(rows[0]["product"], "solar_wind_plasma_2_hour")
        self.assertEqual(rows[0]["time_utc"], "2026-01-01T00:00:00Z")
        self.assertEqual(rows[0]["speed"], "410.2")

    def test_parse_giro_table_normalizes_success_and_error_rows(self):
        text = "\n".join(
            [
                "# Time CS foF2 MUFD hmF2 TEC fmin",
                "# Time CS foF2 QD MUFD QD hmF2 QD TEC QD fmin QD",
                "2012-07-02T21:01:00.000Z 100 5.15 // 15.58 // 311.5 // 3.9 // 1.7 //",
                "ERROR: No data found for requested period",
            ]
        )
        rows = collector.parse_giro_table(text, station="MO155", raw_file="sample.txt")
        self.assertEqual(rows[0]["source"], "GIRO_DIDBase")
        self.assertEqual(rows[0]["station"], "MO155")
        self.assertEqual(rows[0]["time_utc"], "2012-07-02T21:01:00Z")
        self.assertEqual(rows[0]["foF2"], "5.15")
        self.assertEqual(rows[0]["foF2_QD"], "//")
        self.assertEqual(rows[1]["error"], "ERROR: No data found for requested period")

    def test_parse_giro_station_codes_deduplicates_form_options(self):
        html = '<select name="location"><option value="MO155">MOSCOW</option><option value="MO155">MOSCOW</option><option value="BC840">BOULDER</option></select>'
        self.assertEqual(collector.parse_giro_station_codes(html), ["MO155", "BC840"])

    def test_normalize_giro_headers_keeps_quality_columns(self):
        headers = collector.normalize_giro_headers(["Time", "CS", "foF2", "QD", "TEC", "QD"])
        self.assertEqual(headers, ["Time", "CS", "foF2", "foF2_QD", "TEC", "TEC_QD"])

    def test_build_analytical_dataset_uses_giro_mufd_column(self):
        rows = [
            {
                "station": "MO155",
                "time_utc": "2012-07-02T21:01:00Z",
                "foF2": "5.15",
                "MUF(D)": "15.583",
            }
        ]
        dataset = collector.build_analytical_dataset(rows, [], tolerance_minutes=90)
        self.assertEqual(dataset[0]["MUFD_3000_MHz"], "15.583")
        self.assertEqual(dataset[0]["muf_3000_proxy_MHz"], 15.583)


class EndToEndJsonTests(unittest.TestCase):
    def test_main_writes_uniform_json_outputs(self):
        noaa_raw = {
            "https://example.test/kp.json": json.dumps(
                [{"time_tag": "2026-01-01T00:00:00", "estimated_kp": 2.0}]
            ).encode("utf-8"),
            "https://giro.uml.edu/didbase/scaled.php": "\n".join(
                [
                    "# Time CS foF2 MUFD hmF2 TEC fmin",
                    "2026-01-01T00:00:00.000Z 100 5.0 15.0 300.0 10.0 1.5",
                ]
            ).encode("latin-1"),
        }

        def fake_fetch(url, *, data, timeout, attempts=3):
            return noaa_raw[url]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.toml"
            output_dir = root / "out"
            config_path.write_text(
                "\n".join(
                    [
                        "[run]",
                        'output_dir = "out"',
                        "timeout_seconds = 1",
                        "[giro]",
                        "enabled = true",
                        'stations = ["MO155"]',
                        'characteristics = "all"',
                        "muf_distance_km = 3000",
                        "[noaa]",
                        "enabled = true",
                        'endpoints = [{ name = "planetary_k_index_1m", url = "https://example.test/kp.json" }]',
                        "[preprocess]",
                        "join_tolerance_minutes = 90",
                    ]
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                start="2026-01-01T00:00:00Z",
                end="2026-01-01T01:00:00Z",
                output_dir=str(output_dir),
            )
            with (
                patch.object(collector, "parse_args", return_value=args),
                patch.object(collector, "fetch_url", side_effect=fake_fetch),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(collector.main(), 0)

            expected = {
                "noaa_observations.json": "noaa_observations",
                "giro_scaled.json": "giro_scaled",
                "analytical_hf_dataset.json": "analytical_hf_dataset",
            }
            for file_name, dataset in expected.items():
                payload = json.loads((output_dir / "processed" / file_name).read_text(encoding="utf-8"))
                assert_payload_shape(self, payload, dataset, 1)

            manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["outputs"]["json"]), 3)
            self.assertEqual(len(manifest["outputs"]["csv"]), 3)


if __name__ == "__main__":
    unittest.main()
