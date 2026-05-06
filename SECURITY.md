# Security Guide

## 🔒 Protecting Sensitive Information

This project handles sensitive credentials and data. Follow these security best practices:

### 1. Environment Variables

All sensitive configuration is managed through environment variables:
- `MONGO_USER`: MongoDB username
- `MONGO_PASS`: MongoDB password
- `MONGO_HOST`: MongoDB host
- `MONGO_PORT`: MongoDB port
- `DB_NAME`: Database name
- `IP2LOCATION_DB`: Path to IP2Location database

**Never commit credentials to the repository.**

### 2. Setup Instructions

1. **Create a `.env` file** (not tracked by Git):
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` with your credentials**:
   ```
   MONGO_USER=your_username
   MONGO_PASS=your_password
   MONGO_HOST=your_host
   MONGO_PORT=27017
   DB_NAME=your_database
   IP2LOCATION_DB=./data/IP-COUNTRY-REGION-CITY.BIN
   ```

3. **Verify `.env` is in `.gitignore`**:
   ```bash
   cat .gitignore  # Should include .env
   ```

4. **Never share your `.env` file**

### 3. Code Best Practices

✅ **GOOD** - Using secure configuration module:
```python
from secure_config import get_config

config = get_config()
mongo_uri = config.get_mongo_uri()  # Credentials retrieved only when needed
# Credentials are NOT stored in class attributes
# Memory is cleared after use
```

✅ **GOOD** - Credentials from environment variables (via secure_config):
```python
from dotenv import load_dotenv
import os

load_dotenv()
username = os.getenv("MONGO_USER")
password = os.getenv("MONGO_PASS")
```

❌ **BAD** - Storing credentials in class attributes:
```python
# DON'T DO THIS - credentials stay in memory!
self.user = RAW_USER
self.pass = RAW_PASS
```

❌ **BAD** - Hardcoded credentials:
```python
# DON'T DO THIS!
username = "admin"
password = "secret123"
mongo_uri = "mongodb://admin:secret123@host:27017"
```

❌ **BAD** - Credentials in logs:
```python
# DON'T DO THIS - logs contain passwords!
logging.info(f"Connecting with password: {password}")
```

### 4. Secure Configuration Module

The project includes a `secure_config.py` module that:

✅ **Minimizes credential exposure**:
- Credentials are NOT stored as class attributes
- Credentials are only used to build MongoDB URI when needed
- Memory is actively cleared after use
- No credentials logged or printed

✅ **Validates configuration**:
- Checks that required variables are set
- Validates environment on startup
- Raises clear errors if configuration is invalid

✅ **Usage**:
```python
from secure_config import get_config

config = get_config()

# Credentials are retrieved only when needed
mongo_uri = config.get_mongo_uri()

# Get other config
db_config = config.get_db_config()
ip_db_path = config.get_ip_db_path()
```

### 5. What's Protected Against

✅ **Hardcoded credentials in source code**
- Credentials are loaded from environment variables only
- `.env` file is excluded from Git

✅ **Credentials in process list**
- Credentials are not stored long-term in memory
- Temporary variables are cleared after use
- Only the connection URI is kept temporarily

✅ **Credentials in logs**
- Scripts never log passwords or connection strings
- MongoDB URIs are not printed to console
- Error messages don't expose credentials

✅ **Credentials in Git history**
- Security check script scans for hardcoded patterns
- `.env` file is never committed
- Pre-commit validation available

⚠️ **Still requires**:
- `.env` file protection (file permissions)
- Server security (don't run untrusted scripts)
- Network security (use TLS/SSL for MongoDB)
- Regular credential rotation

### 6. Git Configuration

The `.gitignore` file excludes:
- `.env` - Local environment configuration
- `.env.local` - Local overrides
- `*.log` - Log files may contain sensitive info
- Large data files

Verify no credentials are in the repository:
```bash
git log -p --all | grep -i "password\|secret\|key"
```

### 7. Before Pushing to Repository

1. **Check for sensitive data**:
   ```bash
   python scripts/security_check.py
   ```

2. **Manual verification** (optional):
   ```bash
   grep -r "MONGO_USER\|MONGO_PASS\|password" . --include="*.py"
   ```
   Should only match in script files loading from env vars, never hardcoded values

3. **Verify `.env` is excluded**:
   ```bash
   git status
   # Should NOT show .env file
   ```

4. **Review changes before commit**:
   ```bash
   git diff
   git diff --cached
   ```

### 8. Sharing the Project

When sharing with team members:

✅ **DO**:
- Share the `.env.example` template
- Share this security guide
- Provide credentials through secure channels
- Use environment-specific `.env` files

❌ **DON'T**:
- Commit `.env` files
- Share credentials via email or chat
- Commit logs containing sensitive data
- Use default/shared credentials

### 9. If Credentials Are Accidentally Exposed

1. **Immediately change all exposed passwords**
2. **Remove from git history**:
   ```bash
   git filter-branch --force --index-filter \
     'git rm --cached --ignore-unmatch .env' \
     --prune-empty --tag-name-filter cat -- --all
   git push --force-with-lease
   ```
3. **Notify team members**
4. **Review repository access logs**

### 10. References

- [OWASP - Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [12 Factor App - Config](https://12factor.net/config)
- [GitHub - Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)
