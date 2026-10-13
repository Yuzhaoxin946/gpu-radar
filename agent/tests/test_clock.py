import argparse
from datetime import datetime, timezone
import threading
import unittest
from unittest.mock import Mock, patch

from gpu_radar_agent import cli, collector
from gpu_radar_agent.api import APIError
from gpu_radar_agent.server_clock import ServerClock, parse_server_time


class ServerClockTests(unittest.TestCase):
    def test_python36_iso_parser_accepts_utc_and_offsets(self):
        expected = datetime(2026, 10, 13, 0, 0, 0, 123000, timezone.utc)
        self.assertEqual(parse_server_time("2026-10-13T00:00:00.123Z"), expected)
        self.assertEqual(parse_server_time("2026-10-13T08:00:00.123+08:00"), expected)
        self.assertEqual(parse_server_time("2026-10-12T19:00:00.123-0500"), expected)
        for invalid in (None, "2026-10-13T00:00:00", "2026-13-13T00:00:00Z", "2026-10-13T00:00:00+24:00", "2026-10-13T00:00:00+00:99"):
            with self.assertRaises(ValueError):
                parse_server_time(invalid)

    def test_round_trip_midpoint_and_monotonic_elapsed_time(self):
        monotonic = Mock(side_effect=[100.0, 100.0, 102.0, 105.0])
        clock = ServerClock(monotonic=monotonic)
        client = Mock()
        client.request.return_value = {"status": "ok", "server_time": "2026-10-13T00:00:00Z"}
        clock.ensure_synced(client)
        self.assertEqual(clock.timestamp(), "2026-10-13T00:00:04Z")
        client.request.assert_called_once_with("GET", "/health")

    def test_refresh_after_five_minutes_and_discard_anchor_on_failure(self):
        monotonic = Mock(return_value=100.0)
        clock = ServerClock(monotonic=monotonic)
        client = Mock()
        client.request.side_effect = [
            {"status": "ok", "server_time": "2026-10-13T00:00:00Z"},
            APIError(None, "network down"),
            {"status": "ok", "server_time": "2026-10-13T00:05:05Z"},
        ]
        clock.ensure_synced(client)
        monotonic.return_value = 399.0
        clock.ensure_synced(client)
        self.assertEqual(client.request.call_count, 1)
        monotonic.return_value = 400.0
        with self.assertRaises(APIError):
            clock.ensure_synced(client)
        with self.assertRaises(APIError):
            clock.timestamp()
        monotonic.return_value = 405.0
        clock.ensure_synced(client)
        self.assertEqual(clock.timestamp(), "2026-10-13T00:05:05Z")

    def test_invalid_health_time_never_creates_anchor(self):
        for response in ({"status": "ok"}, {"status": "error", "server_time": "2026-10-13T00:00:00Z"}, {"status": "ok", "server_time": "bad"}, None):
            clock = ServerClock()
            client = Mock()
            client.request.return_value = response
            with self.assertRaises(APIError):
                clock.ensure_synced(client)
            with self.assertRaises(APIError):
                clock.timestamp()

    def test_run_uses_backend_time_despite_severely_skewed_host_clock(self):
        client = Mock()
        client.request.side_effect = [{"status": "ok", "server_time": "2026-10-13T00:00:00Z"}, {"accepted": True}]
        args = argparse.Namespace(request_timeout=1, simulate=True, interval=5, once=True)
        config = {"server": "https://radar.example", "name": "lab-a", "device_token": "device-secret"}
        with patch.object(cli, "Client", return_value=client), patch("time.monotonic", return_value=100.0), patch.object(collector, "utc_now", return_value="2099-01-01T00:00:00Z"):
            self.assertEqual(cli.run_loop(args, config, threading.Event()), 0)
        report = client.request.call_args_list[1].args[2]
        self.assertEqual(report["sampled_at"], "2026-10-13T00:00:00Z")

    def test_health_failure_retries_with_backoff_and_sends_no_samples(self):
        client = Mock()
        client.request.side_effect = APIError(503, "unavailable")
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        args = argparse.Namespace(request_timeout=1, simulate=False, interval=5, once=False, collection_timeout=8)
        config = {"server": "https://radar.example", "name": "lab-a", "device_token": "device-secret"}
        with patch.object(cli, "Client", return_value=client), patch.object(cli, "collect_bounded") as collect, patch.object(cli.random, "uniform", return_value=1.0):
            self.assertEqual(cli.run_loop(args, config, stop), 0)
        collect.assert_not_called()
        self.assertEqual([call.args for call in client.request.call_args_list], [("GET", "/health"), ("GET", "/health")])
        self.assertEqual([call.args for call in stop.wait.call_args_list], [(5.0,), (10.0,)])


if __name__ == "__main__":
    unittest.main()
