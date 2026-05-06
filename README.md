# Data Engineering Project

A Python-based data engineering project for processing and analyzing MongoDB data with IP geolocation enrichment.

## Project Structure

```
.
├── scripts/
│   ├── get_info.py         # MongoDB data extraction and processing
│   └── ip2location.py      # IP geolocation lookup and enrichment
├── dump/
│   ├── countly/            # MongoDB BSON dumps
│   │   ├── summary.bson
│   │   └── summary.metadata.json
│   └── glamira_ubl_oct2019_nov2019.tar.gz
├── data/
│   └── IP-COUNTRY-REGION-CITY.BIN  # IP2Location database
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Prerequisites

- Python 3.7+
- MongoDB
- IP2Location database file

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd DE_data
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your MongoDB credentials and paths
```

⚠️ **Important**: Never commit the `.env` file. It's automatically excluded by `.gitignore`.

### Security

For detailed security guidelines, see [SECURITY.md](SECURITY.md).

Key points:
- All credentials are loaded from environment variables
- `.env` file is not tracked by Git
- Scripts validate that required credentials are set
- Both username and password must be provided together


## Configuration

Set the following environment variables:

- `MONGO_USER`: MongoDB username
- `MONGO_PASS`: MongoDB password
- `MONGO_HOST`: MongoDB host (default: localhost)
- `MONGO_PORT`: MongoDB port (default: 27017)
- `DB_NAME`: Database name (default: test)
- `IP2LOCATION_DB`: Path to IP2Location database file

## Usage

### Extract MongoDB Data
```bash
python scripts/get_info.py
```

### Enrich with IP Geolocation
```bash
python scripts/ip2location.py
```

### Security Check
```bash
python scripts/security_check.py
```

Run this before pushing to the repository to ensure no sensitive data is exposed.

## Data Files

- **dump/countly/**: MongoDB BSON backup files
- **data/**: IP2Location database and related files

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]
