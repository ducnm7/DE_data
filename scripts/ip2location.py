import os
import csv
import logging
from pymongo import MongoClient, UpdateOne
from multiprocessing import Pool, cpu_count
from urllib.parse import quote_plus
import IP2Location
import socket
import struct
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    logging.warning("python-dotenv not installed. Using system environment variables only.")

def ip_to_int(ip):
    return struct.unpack("!I", socket.inet_aton(ip))[0]

# =========================
# CONFIG
# =========================
RAW_USER = os.getenv("MONGO_USER", "").strip()
RAW_PASS = os.getenv("MONGO_PASS", "").strip()
HOST = os.getenv("MONGO_HOST", "localhost")
PORT = os.getenv("MONGO_PORT", "27017")
DB_NAME = os.getenv("DB_NAME", "test")
IP_DB_PATH = os.getenv("IP2LOCATION_DB")

BATCH_SIZE = 20000
NUM_WORKERS = cpu_count() * 2
OUTPUT_FILE = "ip_locations_2.csv"

logging.basicConfig(level=logging.INFO)

# =========================
# GLOBAL FOR WORKER
# =========================
ip_db = None


# =========================
# INIT WORKER (LOAD DB 1 LẦN)
# =========================
def init_worker():
    global ip_db
    ip_db = IP2Location.IP2Location(IP_DB_PATH)


# =========================
# MONGO URI
# =========================
def build_mongo_uri():
    if not RAW_USER:
        logging.warning("⚠️  MONGO_USER not set. Connecting without authentication.")
        return f"mongodb://{HOST}:{PORT}/"
    
    if not RAW_PASS:
        logging.error("❌ MONGO_USER is set but MONGO_PASS is not. Cannot proceed.")
        raise ValueError("MONGO_PASS environment variable is required when MONGO_USER is set")

    user = quote_plus(RAW_USER)
    pwd = quote_plus(RAW_PASS)
    return f"mongodb://{user}:{pwd}@{HOST}:{PORT}/?authSource=test"

# =========================
# COUNT UNIQUE IPs
# =========================
def range_scan(ips, ranges):
    results = []

    i = 0  # pointer ip
    j = 0  # pointer range

    while i < len(ips) and j < len(ranges):
        ip_int, ip_str = ips[i]
        ip_from, ip_to, country, city, lat, lon = ranges[j]

        if ip_int < ip_from:
            i += 1
        elif ip_int > ip_to:
            j += 1
        else:
            results.append({
                "ip": ip_str,
                "country": country,
                "city": city,
                "latitude": lat,
                "longitude": lon
            })
            i += 1

    return results


# =========================
# STREAM IP
# =========================
def load_ips(db):
    pipeline = [
        {
            "$match": {
                "ip": {
                    "$ne": None,
                    "$not": {
                        "$regex": r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)"
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$ip"
            }
        }
    ]

    ips = []
    for doc in db.summary.aggregate(pipeline, allowDiskUse=True):
        ip = doc["_id"]
        try:
            ips.append((ip_to_int(ip), ip))
        except:
            continue

    return sorted(ips, key=lambda x: x[0])


# =========================
# WORKER
# =========================
def load_ranges(csv_file):
    ranges = []

    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)

        for row in reader:
            # skip dòng lỗi
            if not row or len(row) < 2:
                continue

            try:
                ip_from = row[0].strip().replace('"', '')
                ip_to = row[1].strip().replace('"', '')

                # skip nếu rỗng
                if not ip_from or not ip_to:
                    continue

                ranges.append((
                    int(ip_from),
                    int(ip_to),
                    row[2] if len(row) > 2 else None,
                    row[3] if len(row) > 3 else None,
                    row[4] if len(row) > 4 else None,
                    row[5] if len(row) > 5 else None
                ))

            except Exception as e:
                continue

    return ranges


# =========================
# CSV WRITE
# =========================
def write_csv(rows, output):
    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["ip", "country", "city", "latitude", "longitude"]
        )
        writer.writeheader()
        writer.writerows(rows)


# =========================
# HANDLE BATCH
# =========================
def handle_batch(db, pool, ip_batch):
    logging.info(f"Processing batch: {len(ip_batch)}")

    chunk_size = 5000
    chunks = [
        ip_batch[i:i + chunk_size]
        for i in range(0, len(ip_batch), chunk_size)
    ]

    results = pool.map(process_batch, chunks)
    final_results = [item for sublist in results for item in sublist]

    logging.info(f"Valid results: {len(final_results)}")

    if not final_results:
        return

    # Mongo upsert (không check trước → nhanh hơn)
    operations = [
        UpdateOne({"ip": r["ip"]}, {"$set": r}, upsert=True)
        for r in final_results
    ]
    db.ip_locations.bulk_write(operations, ordered=False)

    # CSV
    write_csv(final_results, "ip_locations_output.csv")





# =========================
# MAIN
# =========================
def main():
    client = MongoClient("mongodb://localhost:27017/")
    db = client["test"]

    print("Loading IPs...")
    ips = load_ips(db)

    print(f"Total IPs: {len(ips)}")

    print("Loading ranges...")
    ranges = load_ranges("ip2location.csv")

    print("Running range scan...")
    results = range_scan(ips, ranges)

    print(f"Matched: {len(results)}")

    write_csv(results, "output.csv")

    print("DONE")


# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    main()