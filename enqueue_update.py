"""Queue a prepared-data update without running the data pipeline."""
import argparse

from api.schemas import UpdateRequest
from api.services import get_station
from api.update_tasks import create_update_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--sources", default="all")
    args = parser.parse_args()
    request = UpdateRequest(**vars(args))
    get_station(request.station)
    if request.end < request.start:
        parser.error("end must be greater than or equal to start")
    print(create_update_task(request).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
