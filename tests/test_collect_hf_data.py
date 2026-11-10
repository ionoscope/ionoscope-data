import argparse
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import collect_hf_data as collector
from unittest.mock import MagicMock


class FetchPlatformTests(unittest.TestCase):
    def test_live_auxiliary_failure_preserves_giro_and_is_logged(self):
        from datetime import datetime, UTC
        with tempfile.TemporaryDirectory() as directory:
            paths = collector.init_paths({"run": {}}, argparse.Namespace(output_dir=directory))
            now = datetime.now(UTC)
            with patch.object(collector, "collect_giro", return_value=[{"station": "TR169"}]), \
                    patch.object(collector, "collect_omni", side_effect=RuntimeError("offline")):
                result = collector.collect_sources({"omni": {"continue_on_error": True}},
                    paths, now, now, 90, {"giro", "omni"})
                self.assertEqual(result["giro"], [{"station": "TR169"}])
                self.assertEqual(result["omni"], [])
                with self.assertRaises(RuntimeError):
                    collector.collect_sources({}, paths, now, now, 90, {"giro", "omni"})
            event = json.loads((paths.logs / "collection_events.jsonl").read_text().strip())
            self.assertEqual(event["source"], "OMNI")
            self.assertEqual(event["status"], "failed")

    def test_live_gfz_retains_successful_indices_after_timeout(self):
        from datetime import datetime, UTC
        with tempfile.TemporaryDirectory() as directory:
            paths = collector.init_paths({"run": {}}, argparse.Namespace(output_dir=directory))
            config = {"gfz": {"continue_on_error": True, "timeout_seconds": 15,
                              "attempts": 1, "indices": ["Kp", "ap"]}}
            payload = json.dumps({"datetime": ["2026-11-09T06:00:00Z"], "ap": [7]}).encode()
            with patch.object(collector, "fetch_url", side_effect=[RuntimeError("slow"), payload]) as fetch:
                now = datetime.now(UTC)
                rows = collector.collect_gfz_indices(config, paths, now, now, 90)
            self.assertEqual([(r["index"], r["value"]) for r in rows], [("ap", 7)])
            self.assertTrue(all(c.kwargs["timeout"] == 15 and c.kwargs["attempts"] == 1
                                for c in fetch.call_args_list))

    def test_sources_start_concurrently_and_preserve_results(self):
        import threading
        from contextlib import ExitStack
        from datetime import datetime, UTC

        barrier = threading.Barrier(4)
        def source(name):
            def collect(*args):
                barrier.wait(timeout=3)
                return [{"source": name}]
            return collect
        with ExitStack() as stack:
            for name, function in [("noaa", "collect_noaa"), ("gfz", "collect_gfz_indices"),
                                   ("omni", "collect_omni"), ("giro", "collect_giro")]:
                stack.enter_context(patch.object(collector, function, side_effect=source(name)))
            now = datetime.now(UTC)
            results = collector.collect_sources({}, None, now, now, 90,
                                                {"noaa", "gfz", "omni", "giro"})
        self.assertEqual(results, {name: [{"source": name}] for name in results})

    def test_noaa_uses_short_timeout_and_two_attempts(self):
        config = {"noaa": {"endpoints": [{"name": "wind", "url": "https://example.test"}]}}
        with tempfile.TemporaryDirectory() as directory:
            paths = collector.init_paths({"run": {}}, argparse.Namespace(output_dir=directory))
            with patch.object(collector, "fetch_url", side_effect=TimeoutError("slow")) as fetch:
                self.assertEqual(collector.collect_noaa(config, paths, 90), [])
            fetch.assert_called_once_with("https://example.test", data=None, timeout=15, attempts=2)
            events = [json.loads(line) for line in (paths.logs / "collection_events.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["status"], "failed")

    def test_giro_tls_keeps_hostname_and_chain_verification(self):
        import ssl
        context = collector.giro_tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)

    def test_gfz_daily_and_three_hour_ap_have_distinct_raw_paths_on_windows(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            paths = SimpleNamespace(raw_gfz=Path(directory))
            payloads = [json.dumps({'datetime': ['2025-01-01T00:00:00Z'], key: [value]}).encode()
                        for key, value in [('ap', 3), ('Ap', 7)]]
            with patch.object(collector, 'fetch_url', side_effect=payloads):
                rows = collector.collect_gfz_indices({'gfz': {'indices': ['ap', 'Ap']}}, paths,
                    datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 2, tzinfo=timezone.utc), 5)
            names = [row['raw_file'] for row in rows]
            self.assertEqual(len({name.casefold() for name in names}), 2)
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 2)

    def test_parallel_event_writes_preserve_every_record(self):
        from concurrent.futures import ThreadPoolExecutor
        import os
        with tempfile.TemporaryDirectory(dir=os.getenv('IONOSCOPE_TEST_TMPDIR')) as directory:
            path = Path(directory)/'events.jsonl'
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda i: collector.append_jsonl(path, {'event': i}), range(800)))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 800)
        self.assertEqual({row['event'] for row in rows}, set(range(800)))

    def test_queued_request_observes_cooldown_received_while_waiting(self):
        clock = [100.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
            if len(sleeps) == 1:
                collector._GIRO_BLOCKED_UNTIL = 166.0
        with patch.object(collector, '_GIRO_NEXT_REQUEST', 106.0), \
                patch.object(collector, '_GIRO_BLOCKED_UNTIL', 0.0), \
                patch.object(collector.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(collector.time, 'sleep', side_effect=sleep):
            collector.wait_for_giro_slot()
            self.assertEqual(sleeps, [6.0, 60.0])
            self.assertEqual(collector._GIRO_NEXT_REQUEST, 172.0)

    def test_cooldown_waits_instead_of_failing_unattempted_stations(self):
        clock = [100.0]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(collector, '_GIRO_NEXT_REQUEST', 0.0), \
                patch.object(collector, '_GIRO_BLOCKED_UNTIL', 160.0), \
                patch.object(collector.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(collector.time, 'sleep', side_effect=sleep) as pause:
            collector.wait_for_giro_slot()
            pause.assert_called_once_with(60.0)
            self.assertEqual(clock[0], 160.0)

    def test_rate_limit_is_not_retried_in_a_burst(self):
        from urllib.error import HTTPError
        error = HTTPError(collector.GIRO_DATA_URL, 429, 'Too Many Requests', {'Retry-After': '120'}, None)
        with patch.object(collector.os, 'name', 'posix'), \
                patch.object(collector, 'wait_for_giro_slot'), \
                patch.object(collector, '_GIRO_BLOCKED_UNTIL', 0), \
                patch.object(collector, 'urlopen', side_effect=error) as request:
            with self.assertRaises(RuntimeError):
                collector.fetch_url(collector.GIRO_DATA_URL, data=None, timeout=5)
            request.assert_called_once()
            self.assertGreater(collector._GIRO_BLOCKED_UNTIL, 0)

    def test_giro_on_linux_uses_http_without_powershell(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"GIRO response"
        with patch.object(collector.os, "name", "posix"), patch.object(collector, "urlopen", return_value=response) as fetch, patch.object(collector, "fetch_url_with_powershell") as powershell:
            self.assertEqual(collector.fetch_url(collector.GIRO_DATA_URL, data={"location": "TR169"}, timeout=5), b"GIRO response")
        fetch.assert_called_once()
        powershell.assert_not_called()

    def test_giro_on_windows_preserves_powershell_transport(self):
        with patch.object(collector.os, "name", "nt"), patch.object(collector, "fetch_url_with_powershell", return_value=b"ok") as powershell:
            self.assertEqual(collector.fetch_url(collector.GIRO_DATA_URL, data=None, timeout=5), b"ok")
        powershell.assert_called_once()


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
    def test_rtsw_uses_active_spacecraft_and_maps_plasma_fields(self):
        config = {"noaa": {"endpoints": [{"name": "solar_wind_plasma_2_hour", "url": "https://example.test/wind"}]}}
        raw = json.dumps([
            {"time_tag": "2026-10-24T08:00:00", "active": False, "source": "IMAP", "proton_speed": 100},
            {"time_tag": "2026-10-24T08:00:00", "active": True, "source": "ACE", "proton_speed": 400, "proton_density": 5},
        ]).encode()
        with tempfile.TemporaryDirectory() as tmp:
            paths = collector.init_paths({"run": {}}, argparse.Namespace(output_dir=tmp))
            with patch.object(collector, "fetch_url", return_value=raw):
                rows = collector.collect_noaa(config, paths, timeout=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "NOAA_SWPC")
        self.assertEqual(rows[0]["speed"], 400)
        self.assertEqual(rows[0]["density"], 5)

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
                raw_gfz=Path(tmp) / "raw" / "gfz",
                raw_omni=Path(tmp) / "raw" / "omni",
                raw_giro=Path(tmp) / "raw" / "giro",
                processed=Path(tmp) / "processed",
                metadata=Path(tmp) / "metadata",
                logs=Path(tmp) / "logs",
            )
            for path in (paths.raw_noaa, paths.raw_gfz, paths.raw_omni, paths.raw_giro, paths.processed, paths.metadata, paths.logs):
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

    def test_parse_giro_station_options_keeps_station_labels(self):
        html = '<option value="MO155">Moscow Parus-A</option><option value="BC840">Boulder</option>'
        self.assertEqual(
            collector.parse_giro_station_options(html),
            [{"station": "MO155", "name": "Moscow Parus-A"}, {"station": "BC840", "name": "Boulder"}],
        )

    def test_iter_time_windows_splits_long_collection_periods(self):
        start = collector.parse_dt("2024-01-01T00:00:00Z")
        end = collector.parse_dt("2024-03-01T00:00:00Z")
        windows = collector.iter_time_windows(start, end, 31)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0][0], start)
        self.assertEqual(windows[-1][1], end)

    def test_iter_time_windows_never_crosses_calendar_year_boundary(self):
        start = collector.parse_dt("2024-01-01T00:00:00Z")
        end = collector.parse_dt("2026-01-01T00:00:00Z")
        windows = collector.iter_time_windows(start, end, 999)
        self.assertEqual(
            windows,
            [
                (collector.parse_dt("2024-01-01T00:00:00Z"), collector.parse_dt("2025-01-01T00:00:00Z")),
                (collector.parse_dt("2025-01-01T00:00:00Z"), collector.parse_dt("2026-01-01T00:00:00Z")),
            ],
        )

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
        dataset = collector.build_analytical_dataset(rows, [], [], tolerance_minutes=90)
        self.assertEqual(dataset[0]["MUFD_3000_MHz"], "15.583")
        self.assertEqual(dataset[0]["muf_3000_proxy_MHz"], 15.583)

    def test_parse_gfz_index_payload_normalizes_records(self):
        payload = {
            "Kp": [0.667],
            "datetime": ["2024-01-01T00:00:00Z"],
            "status": ["def"],
            "meta": {"source": "GFZ Potsdam"},
        }
        rows = collector.parse_gfz_index_payload(payload, "Kp", "kp.json")
        self.assertEqual(
            rows[0],
            {
                "source": "GFZ",
                "product": "kp_gfz_json_api",
                "index": "Kp",
                "time_utc": "2024-01-01T00:00:00Z",
                "value": 0.667,
                "status": "def",
                "raw_file": "kp.json",
            },
        )

    def test_build_analytical_dataset_adds_nearest_gfz_index_features(self):
        giro_rows = [{"station": "MO155", "time_utc": "2024-01-01T01:00:00Z", "foF2": "5.0"}]
        gfz_rows = [
            {"index": "Kp", "time_utc": "2024-01-01T00:00:00Z", "value": 0.667, "status": "def"},
            {"index": "Ap", "time_utc": "2024-01-01T00:00:00Z", "value": 7, "status": "def"},
        ]
        dataset = collector.build_analytical_dataset(giro_rows, [], gfz_rows, tolerance_minutes=90)
        self.assertEqual(dataset[0]["Kp"], 0.667)
        self.assertEqual(dataset[0]["Kp_time_utc"], "2024-01-01T00:00:00Z")
        self.assertEqual(dataset[0]["Ap"], 7)

    def test_time_join_preserves_boundaries_source_order_and_nearest_ties(self):
        giro = [{"station": "TR169", "time_utc": "2026-01-01T01:00:00Z", "foF2": "5"}]
        noaa = [
            {"product": "solar_wind_plasma_2_hour", "time_utc": timestamp, "speed": speed}
            for timestamp, speed in [
                ("2026-01-01T02:00:00Z", 500),
                ("2026-01-01T00:00:00Z", 400),
                ("2026-01-01T02:00:00Z", 550),
                ("2026-01-01T02:00:01Z", 900),
            ]
        ]
        gfz = [
            {"index": "Kp", "time_utc": "2026-01-01T02:00:00Z", "value": 7},
            {"index": "Kp", "time_utc": "2026-01-01T00:00:00Z", "value": 2},
        ]
        result = collector.build_analytical_dataset(giro, noaa, gfz, tolerance_minutes=60)[0]
        self.assertEqual(result["sw_speed_km_s"], 550)
        self.assertEqual(result["Kp"], 7)
        self.assertEqual(result["Kp_time_utc"], "2026-01-01T02:00:00Z")

    def test_parse_omni_hourly_listing_normalizes_rows(self):
        text = """
        <pre>YEAR DOY HR    1     2
        2024   1  0  -2.8  -11
        2024   1  1 9999.99 99999
        </pre>
        """
        rows = collector.parse_omni_hourly_listing(text, ["bz_gsm_nT", "dst_nT"], "omni.html")
        self.assertEqual(rows[0]["time_utc"], "2024-01-01T00:00:00Z")
        self.assertEqual(rows[0]["bz_gsm_nT"], -2.8)
        self.assertEqual(rows[0]["dst_nT"], -11.0)
        self.assertEqual(rows[1]["bz_gsm_nT"], "")


class EndToEndJsonTests(unittest.TestCase):
    def test_main_writes_uniform_json_outputs(self):
        noaa_raw = {
            "https://example.test/kp.json": json.dumps(
                [{"time_tag": "2026-01-01T00:00:00", "estimated_kp": 2.0}]
            ).encode("utf-8"),
            "https://kp.gfz.de/app/json/?start=2026-01-01T00%3A00%3A00Z&end=2026-01-01T01%3A00%3A00Z&index=Kp": json.dumps(
                {"Kp": [2.0], "datetime": ["2026-01-01T00:00:00Z"], "status": ["def"]}
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
                        "chunk_days = 31",
                        "[noaa]",
                        "enabled = true",
                        'endpoints = [{ name = "planetary_k_index_1m", url = "https://example.test/kp.json" }]',
                        "[gfz]",
                        "enabled = true",
                        'api_url = "https://kp.gfz.de/app/json/"',
                        'indices = ["Kp"]',
                        "[omni]",
                        "enabled = false",
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
                sources="all",
            )
            with (
                patch.object(collector, "parse_args", return_value=args),
                patch.object(collector, "fetch_url", side_effect=fake_fetch),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(collector.main(), 0)

            expected = {
                "noaa_observations.json": "noaa_observations",
                "geophysical_indices.json": "geophysical_indices",
                "giro_scaled.json": "giro_scaled",
                "analytical_hf_dataset.json": "analytical_hf_dataset",
            }
            for file_name, dataset in expected.items():
                payload = json.loads((output_dir / "processed" / file_name).read_text(encoding="utf-8"))
                assert_payload_shape(self, payload, dataset, 1)

            manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["outputs"]["json"]), 5)
            self.assertEqual(len(manifest["outputs"]["csv"]), 5)


if __name__ == "__main__":
    unittest.main()
