import requests
from html.parser import HTMLParser
import os
import logging
import csv
import socket
import struct
import time
import random
from pymongo import MongoClient, UpdateOne
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading
import json
from multiprocessing import cpu_count

# Import secure configuration
from secure_config import get_config

# =========================
# CONFIG
# =========================
config = get_config()

# Validate configuration on startup
if not config.validate():
    raise RuntimeError("Configuration validation failed. Check your .env file.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.info("✅ Secure configuration loaded")

# Product crawling config
MAX_WORKERS = 8
MAX_INFLIGHT = 200
BATCH_SIZE = 500
MAX_RETRY = 3

# IP location config
IP_BATCH_SIZE = 20000
NUM_WORKERS = cpu_count() * 2

# Output files
PRODUCT_CSV_OUTPUT = "product_info.csv"
IP_LOCATION_CSV_OUTPUT = "ip_locations.csv"

# =========================
# THREAD LOCAL SESSION
# =========================
thread_local = threading.local()

def get_session():
    """Get thread-local HTTP session for requests"""
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session

# =========================
# SECURE MONGODB CONNECTION
# =========================
def get_mongo_client():
    """
    Create MongoDB client with credentials from secure config
    Credentials are never stored - only used to build URI once
    """
    try:
        mongo_uri = config.get_mongo_uri()
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        # Verify connection
        client.server_info()
        logging.info("✅ Connected to MongoDB")
        return client
    except Exception as e:
        logging.error(f"❌ Failed to connect to MongoDB: {e}")
        raise

# =========================
# UTILITY FUNCTIONS
# =========================
def ip_to_int(ip):
    """Convert IP address string to integer"""
    return struct.unpack("!I", socket.inet_aton(ip))[0]

# =========================
# PARSER FOR PRODUCT DATA
# =========================
class ReactDataParser(HTMLParser):
    """Parse React data from HTML script tags"""
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.react_data = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self.in_script = True

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and "var react_data =" in data:
            start = data.find("{")
            end = data.rfind("}") + 1
            if start != -1 and end != -1:
                try:
                    self.react_data = json.loads(data[start:end])
                except json.JSONDecodeError as e:
                    logging.debug(f"Failed to parse react_data JSON: {e}")

def extract_react_data(html):
    """Extract react data from HTML content"""
    parser = ReactDataParser()
    parser.feed(html)
    return parser.react_data

# =========================
# PRODUCT CRAWLING FUNCTIONS
# =========================
def load_product_ids(db):
    """Load unique product IDs from MongoDB"""
    pipeline = [
        {
            "$match": {
                "collection": {
                    "$in": [
                        "view_product_detail",
                        "select_product_option",
                        "select_product_option_quality",
                        "add_to_cart_action",
                        "product_detail_recommendation_visible",
                        "product_detail_recommendation_noticed",
                        "product_view_all_recommend_clicked"
                    ]
                },
                "product_id": {"$exists": True, "$ne": None, "$ne": ""}
            }
        },
        {
            "$group": {
                "_id": "$product_id"
            }
        },
        {
            "$project": {
                "_id": 0,
                "product_id": "$_id"
            }
        }
    ]

    try:
        docs = list(db.summary.aggregate(pipeline, allowDiskUse=True))
        logging.info(f"✅ Loaded {len(docs)} unique product_ids from MongoDB")
        return docs
    except Exception as e:
        logging.error(f"❌ Failed to load product_ids: {e}")
        return []

def crawl_one_product(info):
    """Crawl product information from website"""
    product_id = info["product_id"]
    url = f"https://www.glamira.com/catalog/product/view/id/{product_id}"
    session = get_session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(1, MAX_RETRY + 1):
        try:
            res = session.get(url, headers=headers, timeout=10)
            logging.info(f"FETCH {product_id} -> {res.status_code} size={len(res.text)}")

            if random.random() < 0.01:
                logging.info(f"[{product_id}] status={res.status_code}, len={len(res.text)}")

            if res.status_code != 200:
                raise Exception(f"HTTP {res.status_code}")

            react_data = extract_react_data(res.text)
            if not react_data:
                logging.warning(f"NO PARSED DATA {product_id}")
                raise Exception("No react_data")

            time.sleep(random.uniform(0.5, 1.5))

            return {
                "product_id": product_id,
                "name": react_data.get("name"),
                "sku": react_data.get("sku"),
                "price": react_data.get("price"),
                "min_price": react_data.get("min_price"),
                "max_price": react_data.get("max_price"),
                "collection": react_data.get("collection"),
                "category_name": react_data.get("category_name"),
                "gender": react_data.get("gender"),
                "quick_options": react_data.get("quick_options", [])
            }

        except Exception as e:
            if attempt == MAX_RETRY:
                logging.warning(f"[{product_id}] FAIL: {e}")
                return None

            time.sleep(2 ** attempt + random.uniform(0, 1))

def write_mongo_batch(db, rows):
    """Write batch of products to MongoDB"""
    if not rows:
        return

    ops = [
        UpdateOne(
            {"product_id": r["product_id"]},
            {"$set": r},
            upsert=True
        )
        for r in rows
    ]

    try:
        db.product_info.bulk_write(ops, ordered=False)
        logging.info(f"✅ Wrote {len(ops)} products to MongoDB")
    except Exception as e:
        logging.error(f"❌ Mongo write error: {e}")

def write_products_csv(rows, output_file=PRODUCT_CSV_OUTPUT):
    """Export products to CSV file"""
    if not rows:
        logging.warning("No product data to write to CSV")
        return
    
    try:
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "product_id", "name", "sku", "price", "min_price", "max_price",
                "collection", "category_name", "gender", "quick_options"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for row in rows:
                # Convert quick_options to string for CSV
                row_copy = row.copy()
                if isinstance(row_copy.get("quick_options"), list):
                    row_copy["quick_options"] = json.dumps(row_copy["quick_options"])
                writer.writerow({k: row_copy.get(k, '') for k in fieldnames})
        
        logging.info(f"✅ Wrote {len(rows)} products to {output_file}")
    except Exception as e:
        logging.error(f"❌ Failed to write products to CSV: {e}")

def crawl_products_pipeline(db, infos, save_to_mongo=True, save_to_csv=True):
    """Main pipeline for crawling products"""
    batch = []
    csv_batch = []
    total = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = set()

        for i, info in enumerate(infos):
            futures.add(executor.submit(crawl_one_product, info))

            if len(futures) >= MAX_INFLIGHT:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)

                for future in done:
                    try:
                        r = future.result()
                        if r:
                            batch.append(r)
                            csv_batch.append(r)
                    except Exception as e:
                        logging.error(f"Thread error: {e}")

            if len(batch) >= BATCH_SIZE:
                if save_to_mongo:
                    write_mongo_batch(db, batch)
                total += len(batch)
                logging.info(f"Processed {total}/{len(infos)}")
                batch = []

            if i % 1000 == 0 and i > 0:
                logging.info(f"Progress: {i}/{len(infos)}")

        # Flush remaining futures
        for future in futures:
            try:
                r = future.result()
                if r:
                    batch.append(r)
                    csv_batch.append(r)
            except Exception as e:
                logging.error(f"Error retrieving future result: {e}")

    if batch and save_to_mongo:
        write_mongo_batch(db, batch)
        total += len(batch)

    if save_to_csv:
        write_products_csv(csv_batch)

    logging.info(f"✅ Total products processed: {total}")

# =========================
# IP LOCATION FUNCTIONS
# =========================
def load_ips_from_mongo(db):
    """Load unique IPs from MongoDB (excluding private IPs)"""
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
        except Exception as e:
            logging.debug(f"Invalid IP: {ip} - {e}")
            continue

    sorted_ips = sorted(ips, key=lambda x: x[0])
    logging.info(f"✅ Loaded {len(sorted_ips)} unique IPs from MongoDB")
    return sorted_ips

def load_ip_ranges(csv_file):
    """Load IP ranges from CSV file"""
    ranges = []

    if not os.path.exists(csv_file):
        logging.error(f"❌ IP ranges file not found: {csv_file}")
        return ranges

    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)

        for row in reader:
            # Skip empty or invalid rows
            if not row or len(row) < 2:
                continue

            try:
                ip_from = row[0].strip().replace('"', '')
                ip_to = row[1].strip().replace('"', '')

                # Skip if empty
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

    logging.info(f"✅ Loaded {len(ranges)} IP ranges from {csv_file}")
    return ranges

def range_scan(ips, ranges):
    """Match IPs with their geographic locations"""
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

def write_ip_locations_csv(rows, output_file=IP_LOCATION_CSV_OUTPUT):
    """Export IP locations to CSV file"""
    if not rows:
        logging.warning("No IP location data to write to CSV")
        return
    
    try:
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["ip", "country", "city", "latitude", "longitude"]
            )
            writer.writeheader()
            writer.writerows(rows)
        
        logging.info(f"✅ Wrote {len(rows)} IP locations to {output_file}")
    except Exception as e:
        logging.error(f"❌ Failed to write IP locations to CSV: {e}")

def process_ip_locations(db, ip_ranges_csv="ip2location.csv", save_to_mongo=True, save_to_csv=True):
    """Main pipeline for processing IP locations"""
    logging.info("=" * 50)
    logging.info("Starting IP Location Processing")
    logging.info("=" * 50)

    # Load IPs from MongoDB
    ips = load_ips_from_mongo(db)
    if not ips:
        logging.warning("No IPs found in MongoDB")
        return

    # Load IP ranges from CSV
    ranges = load_ip_ranges(ip_ranges_csv)
    if not ranges:
        logging.warning(f"No IP ranges loaded from {ip_ranges_csv}")
        return

    # Perform range scan
    logging.info("Running range scan to match IPs with locations...")
    results = range_scan(ips, ranges)
    
    logging.info(f"✅ Matched {len(results)} IPs with locations")

    # Save to CSV
    if save_to_csv:
        write_ip_locations_csv(results)

    # Save to MongoDB
    if save_to_mongo and results:
        try:
            ops = [
                UpdateOne(
                    {"ip": r["ip"]},
                    {"$set": r},
                    upsert=True
                )
                for r in results
            ]
            db.ip_locations.bulk_write(ops, ordered=False)
            logging.info(f"✅ Wrote {len(results)} IP locations to MongoDB")
        except Exception as e:
            logging.error(f"❌ Failed to write IP locations to MongoDB: {e}")

# =========================
# MAIN FUNCTION
# =========================
def main():
    """Main entry point with options for product crawling and IP location processing"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Combined Product Crawler and IP Location Processor')
    parser.add_argument('--products', action='store_true', help='Crawl product information')
    parser.add_argument('--ips', action='store_true', help='Process IP locations')
    parser.add_argument('--both', action='store_true', help='Do both product crawling and IP processing')
    parser.add_argument('--csv-only', action='store_true', help='Only export to CSV, skip MongoDB writes')
    parser.add_argument('--mongo-only', action='store_true', help='Only write to MongoDB, skip CSV export')
    parser.add_argument('--ip-ranges', default='ip2location.csv', help='Path to IP ranges CSV file')
    
    args = parser.parse_args()

    # Default: do both if no specific option
    if not args.products and not args.ips and not args.both:
        args.both = True

    try:
        # Get secure MongoDB connection
        client = get_mongo_client()
        
        # Get database name from secure config
        db_config = config.get_db_config()
        db = client[db_config["db_name"]]

        # Determine MongoDB and CSV settings
        save_to_mongo = not args.csv_only
        save_to_csv = not args.mongo_only

        # Product crawling
        if args.products or args.both:
            logging.info("=" * 50)
            logging.info("Starting Product Crawling")
            logging.info("=" * 50)
            product_ids = load_product_ids(db)
            if product_ids:
                crawl_products_pipeline(db, product_ids, save_to_mongo=save_to_mongo, save_to_csv=save_to_csv)
            else:
                logging.warning("No product IDs found")

        # IP location processing
        if args.ips or args.both:
            process_ip_locations(db, args.ip_ranges, save_to_mongo=save_to_mongo, save_to_csv=save_to_csv)

        # Create indexes
        if save_to_mongo:
            try:
                db.product_info.create_index([("product_id", 1)], unique=True)
                db.ip_locations.create_index([("ip", 1)], unique=True)
                logging.info("✅ Created MongoDB indexes")
            except Exception as e:
                logging.debug(f"Index creation issue (may already exist): {e}")

        logging.info("✅ DONE - All operations completed successfully")
        
    except Exception as e:
        logging.error(f"❌ Error in main: {e}")
        raise
    finally:
        if 'client' in locals():
            client.close()
            logging.info("MongoDB connection closed")

if __name__ == "__main__":
    main()
