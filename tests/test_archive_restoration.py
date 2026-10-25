from datetime import datetime, timezone

from scripts.restore_station_archive import valid_archive


def test_cached_archive_must_match_station_and_requested_period():
    start=datetime(2024,8,24,tzinfo=timezone.utc)
    end=datetime(2025,1,1,tzinfo=timezone.utc)
    raw=b'# GIRO\n# URSI-Code TR169\n# 2024-08-24T00:00 - 2025-01-01T00:00\n'
    assert valid_archive(raw,'TR169',start,end)
    assert not valid_archive(raw,'AT138',start,end)
    assert not valid_archive(raw,'TR169',start,datetime(2025,2,1,tzinfo=timezone.utc))
    assert not valid_archive(raw+b'ERROR: query rejected','TR169',start,end)
    assert not valid_archive(b'<html>Service unavailable</html>','TR169',start,end)
