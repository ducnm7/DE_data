# Data Directory

This directory contains data files used by the project.

## Files

- **IP-COUNTRY-REGION-CITY.BIN**: IP2Location database file for geolocation lookups

## Setup

1. Place the `IP-COUNTRY-REGION-CITY.BIN` file in this directory
2. Update the `.env` file with the correct path if needed:
   ```
   IP2LOCATION_DB=./data/IP-COUNTRY-REGION-CITY.BIN
   ```
