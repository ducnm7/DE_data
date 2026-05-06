"""
Secure configuration module
Loads and validates sensitive configuration with security best practices
"""

import os
import logging
import threading
from pathlib import Path
from typing import Tuple, Optional

# Don't log passwords
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("pymongo").setLevel(logging.WARNING)

# Thread-safe singleton lock
_config_lock = threading.Lock()


class SecureConfig:
    """
    Manages sensitive configuration securely
    - Minimizes credential exposure in memory
    - Validates configuration
    - Provides secure credential handling
    """
    
    def __init__(self):
        self._mongo_uri = None
        self._loaded = False
        self._load_from_env()
    
    def _load_from_env(self):
        """Load configuration from environment variables"""
        try:
            from dotenv import load_dotenv
            env_path = Path(__file__).parent.parent / ".env"
            load_dotenv(dotenv_path=env_path)
        except ImportError:
            logging.warning("python-dotenv not installed. Using system environment variables.")
        
        self._loaded = True
    
    def _get_env_var(self, name: str, default: str = "", required: bool = False) -> str:
        """
        Safely get environment variable without logging its value
        """
        value = os.getenv(name, default).strip()
        
        if required and not value:
            raise ValueError(f"Required environment variable '{name}' is not set")
        
        return value
    
    def get_mongo_uri(self) -> str:
        """
        Build MongoDB URI with credentials from environment
        Credentials are NOT stored as class attributes - only generated when needed
        """
        from urllib.parse import quote_plus
        
        user = self._get_env_var("MONGO_USER")
        password = self._get_env_var("MONGO_PASS")
        host = self._get_env_var("MONGO_HOST", "localhost")
        port = self._get_env_var("MONGO_PORT", "27017")
        
        if not user:
            logging.warning("⚠️  MONGO_USER not set. Connecting without authentication.")
            return f"mongodb://{host}:{port}/"
        
        if not password:
            raise ValueError(
                "❌ MONGO_USER is set but MONGO_PASS is not. "
                "Cannot proceed - both are required."
            )
        
        # Encode credentials to be safe in URL
        safe_user = quote_plus(user)
        safe_pass = quote_plus(password)
        
        # Build URI - credentials not logged
        uri = f"mongodb://{safe_user}:{safe_pass}@{host}:{port}/?authSource=test"
        
        # Clear from memory if possible
        del user, password, safe_user, safe_pass
        
        return uri
    
    def get_db_config(self) -> dict:
        """Get database configuration"""
        return {
            "host": self._get_env_var("MONGO_HOST", "localhost"),
            "port": int(self._get_env_var("MONGO_PORT", "27017")),
            "db_name": self._get_env_var("DB_NAME", "test"),
        }
    
    def get_ip_db_path(self) -> str:
        """Get IP2Location database path"""
        path = self._get_env_var("IP2LOCATION_DB", required=False)
        
        if not path:
            logging.warning("⚠️  IP2LOCATION_DB not set")
            return ""
        
        if not os.path.exists(path):
            raise FileNotFoundError(f"IP2Location database not found at: {path}")
        
        return path
    
    def validate(self) -> bool:
        """Validate all required configuration"""
        try:
            # Try to build MongoDB URI - will raise if invalid
            _ = self.get_mongo_uri()
            logging.info("✅ Configuration validated successfully")
            return True
        except ValueError as e:
            logging.error(f"❌ Configuration validation failed: {e}")
            return False


# Singleton instance
_config = None


def get_config() -> SecureConfig:
    """Get global configuration instance (thread-safe singleton)"""
    global _config
    if _config is None:
        with _config_lock:
            # Double-check pattern for thread safety
            if _config is None:
                _config = SecureConfig()
    return _config
