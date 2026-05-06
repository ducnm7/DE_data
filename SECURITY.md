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

✅ **GOOD** - Credentials from environment variables:
```python
from dotenv import load_dotenv
import os

load_dotenv()
username = os.getenv("MONGO_USER")
password = os.getenv("MONGO_PASS")
```

❌ **BAD** - Hardcoded credentials:
```python
# DON'T DO THIS!
username = "admin"
password = "secret123"
```

### 4. Validation

Both scripts validate that:
- If `MONGO_USER` is set, `MONGO_PASS` must also be set
- Missing credentials trigger warnings or errors
- Scripts fail fast with clear error messages

### 5. Git Configuration

The `.gitignore` file excludes:
- `.env` - Local environment configuration
- `.env.local` - Local overrides
- `*.log` - Log files may contain sensitive info
- Large data files

Verify no credentials are in the repository:
```bash
git log -p --all | grep -i "password\|secret\|key"
```

### 6. Before Pushing to Repository

1. **Check for sensitive data**:
   ```bash
   grep -r "MONGO_USER\|MONGO_PASS\|password" . --include="*.py"
   ```
   Should only match in script files loading from env vars, never hardcoded values

2. **Verify `.env` is excluded**:
   ```bash
   git status
   # Should NOT show .env file
   ```

3. **Review changes before commit**:
   ```bash
   git diff
   git diff --cached
   ```

### 7. Sharing the Project

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

### 8. If Credentials Are Accidentally Exposed

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

### 9. References

- [OWASP - Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [12 Factor App - Config](https://12factor.net/config)
- [GitHub - Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)
