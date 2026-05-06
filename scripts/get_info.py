import requests
from html.parser import HTMLParser
import os
import logging
from pymongo import MongoClient, UpdateOne
from urllib.parse import quote_plus
import time
import random
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading
import json
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    logging.warning("python-dotenv not installed. Using system environment variables only.")

# =========================
# CONFIG
# =========================
RAW_USER = os.getenv("MONGO_USER", "").strip()
RAW_PASS = os.getenv("MONGO_PASS", "").strip()
HOST = os.getenv("MONGO_HOST", "localhost")
PORT = os.getenv("MONGO_PORT", "27017")
DB_NAME = os.getenv("DB_NAME", "test")

MAX_WORKERS = 8              
MAX_INFLIGHT = 200          
BATCH_SIZE = 500
MAX_RETRY = 3

logging.basicConfig(level=logging.INFO)

# =========================
# THREAD LOCAL SESSION
# =========================
thread_local = threading.local()

def get_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session

# =========================
# MONGO
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

def load_product_id(db):
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
        logging.info(f"Loaded {len(docs)} unique product_ids from MongoDB")
        return docs
    except Exception as e:
        logging.error(f"Failed to load product_ids: {e}")
        return []

# =========================
# PARSER
# =========================
class ReactDataParser(HTMLParser):
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
                except:
                    pass

def extract_react_data(html):
    parser = ReactDataParser()
    parser.feed(html)
    return parser.react_data

# =========================
# CRAWL
# =========================
def crawl_one(info):
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
                logging.info(f"NO PARSED DATA {product_id}")
            if not react_data:
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

# =========================
# MONGO WRITE
# =========================
def write_mongo_batch(db, rows):
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
    except Exception as e:
        logging.error(f"Mongo write error: {e}")

# =========================
# MAIN PIPELINE
# =========================
def crawl_product_information(db, infos):
    batch = []
    total = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = set()

        for i, info in enumerate(infos):
            futures.add(executor.submit(crawl_one, info))

            if len(futures) >= MAX_INFLIGHT:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)

                for future in done:
                    try:
                        r = future.result()
                        if r:
                            batch.append(r)
                    except Exception as e:
                        logging.error(f"Thread error: {e}")

            if len(batch) >= BATCH_SIZE:
                write_mongo_batch(db, batch)
                total += len(batch)
                logging.info(f"Inserted {total}")
                batch = []

            if i % 1000 == 0:
                logging.info(f"Processed {i}")

        # flush remaining
        for future in futures:
            try:
                r = future.result()
                if r:
                    batch.append(r)
            except:
                pass

    if batch:
        write_mongo_batch(db, batch)
        total += len(batch)

    logging.info(f"Total inserted: {total}")

# =========================
# MAIN
# =========================
def main():
    client = MongoClient(build_mongo_uri())
    db = client[DB_NAME]

    print("Start crawling...")
    infos = load_product_id(db)   # STREAM

    crawl_product_information(db, infos)

    db.product_info.create_index([("product_id", 1)], unique=True)

    print("DONE")

if __name__ == "__main__":
    main()