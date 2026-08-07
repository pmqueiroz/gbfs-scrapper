#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

STATION_IDS = ["61", "146", "36"]
#   Salvador:       https://salvador.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json
#   Rio de Janeiro: https://riodejaneiro.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json
#   Sao Paulo:      https://saopaulo.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json
#   Recife:         https://recife.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json
#   Curitiba:       https://curitiba.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json
#   Santiago (CL):  https://santiago.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json
CITY_GBFS_URL = "https://salvador.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json"
CSV_FILENAME = "station_history.csv"

CSV_HEADER = [
    "timestamp",
    "station_id",
    "station_name",
    "normal_bikes",
    "electric_bikes",
    "docks_available",
]
TIMEZONE = "America/Sao_Paulo"
REQUEST_TIMEOUT = 30
RETRIES = 3

def fetch_json(url, session, retries=RETRIES):
    headers = {
        "User-Agent": "gbfs-scrapper/1.0 (GitHub Actions monitoring script)"
    }
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            print(f"  Attempt {attempt}/{retries} failed for {url}: {exc}")
            if attempt < retries:
                continue
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {last_error}")


def get_feed_url(gbfs_root, feed_name):
    feeds = (gbfs_root.get("data") or {}).get("feeds", [])
    for feed in feeds:
        if feed.get("name") == feed_name:
            return feed.get("url")
    raise RuntimeError(f"Feed '{feed_name}' not found in the auto-discovery file.")


def extract_station_name(station):
    name = station.get("name")
    if isinstance(name, list):
        for item in name:
            if isinstance(item, dict) and item.get("language") == "pt":
                return item.get("text", "")
        for item in name:
            if isinstance(item, dict) and item.get("text"):
                return item["text"]
        return ""
    return name or ""


def build_vehicle_type_map(vehicle_types_data):
    type_map = {}
    for vtype in (vehicle_types_data.get("data") or {}).get("vehicle_types", []):
        type_map[vtype.get("vehicle_type_id")] = vtype.get("propulsion_type", "")
    return type_map


def split_bikes_by_type(vehicle_types_available, type_map):
    total = 0
    electric = 0
    for item in vehicle_types_available or []:
        vtype_id = item.get("vehicle_type_id")
        count = int(item.get("count") or 0)
        total += count
        if "electric" in type_map.get(vtype_id, ""):
            electric += count
    normal = total - electric
    return normal, electric


def describe_status(station):
    installed = station.get("is_installed", True)
    renting = station.get("is_renting", True)
    returning = station.get("is_returning", True)
    if not installed:
        return "out_of_service"
    if renting and returning:
        return "active"
    if renting:
        return "rental_only"
    if returning:
        return "return_only"
    return "paused"


def prepare_csv(filename, header):
    if not os.path.isfile(filename):
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            csv.writer(csvfile).writerow(header)
        return "file created with the new header"

    with open(filename, newline="", encoding="utf-8") as csvfile:
        first_line = csvfile.readline().rstrip("\r\n")

    if first_line != ",".join(header):
        backup = f"{filename}.backup"
        os.replace(filename, backup)
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            csv.writer(csvfile).writerow(header)
        return f"stale header; old data moved to {backup}"

    return "header ok"

def main():
    print(f"Monitoring {len(STATION_IDS)} station(s): {', '.join(STATION_IDS)}")
    print(f"Feed: {CITY_GBFS_URL}")

    session = requests.Session()

    print("Fetching auto-discovery file (gbfs.json)...")
    gbfs_root = fetch_json(CITY_GBFS_URL, session)
    status_url = get_feed_url(gbfs_root, "station_status")
    info_url = get_feed_url(gbfs_root, "station_information")
    vehicle_types_url = get_feed_url(gbfs_root, "vehicle_types")
    print(f"station_status URL: {status_url}")

    type_map = {}
    if vehicle_types_url:
        try:
            type_map = build_vehicle_type_map(fetch_json(vehicle_types_url, session))
        except RuntimeError as exc:
            print(f"  (warning) vehicle_types feed unavailable; no type breakdown: {exc}")

    print("Fetching station_status...")
    status_data = fetch_json(status_url, session)
    stations_status = (status_data.get("data") or {}).get("stations", [])
    status_by_id = {s.get("station_id"): s for s in stations_status}

    name_by_id = {}
    if info_url:
        try:
            info_data = fetch_json(info_url, session)
            info_stations = (info_data.get("data") or {}).get("stations", [])
            for info in info_stations:
                name_by_id[info.get("station_id")] = extract_station_name(info)
        except RuntimeError as exc:
            print(f"  (warning) could not fetch station names: {exc}")

    timestamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    print(prepare_csv(CSV_FILENAME, CSV_HEADER))

    found = 0
    rows = []
    for station_id in STATION_IDS:
        station = status_by_id.get(station_id)
        if station is None:
            print(f"  [WARNING] station {station_id} not found in the feed; skipped.")
            continue

        found += 1
        station_name = name_by_id.get(station_id, station_id)
        normal, electric = split_bikes_by_type(
            station.get("vehicle_types_available"), type_map
        )
        if normal + electric == 0:
            normal = int(station.get("num_vehicles_available") or 0)
        docks = int(station.get("num_docks_available") or 0)
        status = describe_status(station)

        print("------------------------------------------")
        print(f"  Timestamp          : {timestamp}")
        print(f"  Station            : {station_name} (ID {station_id})")
        print(f"  Status             : {status}")
        print(f"  Normal bikes       : {normal}")
        print(f"  Electric bikes     : {electric}")
        print(f"  Docks available    : {docks}")

        rows.append([timestamp, station_id, station_name, normal, electric, docks])

    print("------------------------------------------")
    if found == 0:
        raise RuntimeError(
            f"No stations found. Monitored IDs: {', '.join(STATION_IDS)}. "
            "Please check the IDs (they may be numeric strings, e.g. '61')."
        )

    if rows:
        with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as csvfile:
            csv.writer(csvfile).writerows(rows)
    print(f"{len(rows)} record(s) saved to {CSV_FILENAME}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
