import csv
import logging
from pymongo import MongoClient
from multiprocessing import cpu_count
import socket
import struct

# Import secure configuration
from secure_config import get_config

# Configure logging
logging.basicConfig(level=logging.INFO)

# Import secure config
config = get_config()

# Validate configuration on startup
if not config.validate():
    raise RuntimeError("Configuration validation failed. Check your .env file.")

logging.info("✅ Secure configuration loaded")

def ip_to_int(ip):
    return struct.unpack("!I", socket.inet_aton(ip))[0]


# =========================
# CONFIG
# =========================
BATCH_SIZE = 20000
NUM_WORKERS = cpu_count() * 2
OUTPUT_FILE = "ip_locations_2.csv"

# =========================
# SECURE MONGODB CONNECTION
# =========================
def get_mongo_client():
    """
    Create MongoDB client with credentials from secure config
    """
    try:
        mongo_uri = config.get_mongo_uri()
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        # Verify connection
        client.server_info()
        logging.info("Connected to MongoDB")
        return client
    except Exception as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
        raise

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

            except ValueError as e:
                logging.debug(f"Skipped invalid IP range row: {e}")
                continue
            except Exception as e:
                logging.error(f"Unexpected error parsing IP range: {e}")
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
# MAIN
# =========================
def main():
    try:
        # Get secure MongoDB connection
        client = get_mongo_client()
        
        # Get database name from secure config
        db_config = config.get_db_config()
        db = client[db_config["db_name"]]

        logging.info("Loading IPs...")
        ips = load_ips(db)

        logging.info(f"Total IPs: {len(ips)}")

        logging.info("Loading ranges...")
        ranges = load_ranges("ip2location.csv")

        logging.info("Running range scan...")
        results = range_scan(ips, ranges)

        logging.info(f"Matched: {len(results)}")

        write_csv(results, "output.csv")

        logging.info("DONE")
        
    except Exception as e:
        logging.error(f"Error in main: {e}")
        raise
    finally:
        if 'client' in locals():
            client.close()
            logging.info("MongoDB connection closed")


# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    main()