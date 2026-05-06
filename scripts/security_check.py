#!/usr/bin/env python3
"""
Security Check Script
Verifies that no sensitive credentials are hardcoded in the repository.
"""

import os
import sys
import re
from pathlib import Path

def check_files_for_secrets(directory="."):
    """
    Scan Python files for hardcoded secrets patterns
    """
    secrets_patterns = [
        r'MONGO_USER\s*=\s*["\'](?!{)',  # MONGO_USER = "value"
        r'MONGO_PASS\s*=\s*["\'](?!{)',  # MONGO_PASS = "value"
        r'password\s*=\s*["\'][\w\-_.]+["\']',  # password = "value"
        r'api[_-]?key\s*=\s*["\']',  # api_key = "value"
        r'secret\s*=\s*["\']',  # secret = "value"
    ]
    
    issues = []
    
    for py_file in Path(directory).rglob("*.py"):
        # Skip virtual environments and security check script itself
        if any(part in py_file.parts for part in ['venv', 'env', '.venv', '__pycache__']):
            continue
        if 'security_check' in str(py_file):
            continue
        
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
                
                for line_num, line in enumerate(lines, 1):
                    # Skip comments and environment variable loading
                    if 'os.getenv' in line or line.strip().startswith('#'):
                        continue
                    # Skip regex patterns and raw strings
                    if 'r["\']' in line or "r'" in line or 'r"' in line:
                        continue
                    
                    for pattern in secrets_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            issues.append({
                                'file': str(py_file),
                                'line': line_num,
                                'content': line.strip()
                            })
        except Exception as e:
            print(f"⚠️  Error reading {py_file}: {e}")
    
    return issues

def check_git_history():
    """
    Check if git is available and warn about history
    """
    if not os.path.exists('.git'):
        return False
    
    try:
        os.system('git log -p --all | grep -i "password\\|secret\\|MONGO_PASS" > /dev/null 2>&1')
        return True
    except:
        return False

def main():
    print("=" * 60)
    print("🔒 Security Verification")
    print("=" * 60)
    
    # Check for hardcoded secrets
    print("\n✓ Checking for hardcoded credentials...")
    issues = check_files_for_secrets()
    
    if issues:
        print(f"\n❌ Found {len(issues)} potential security issue(s):\n")
        for issue in issues:
            print(f"  File: {issue['file']}")
            print(f"  Line {issue['line']}: {issue['content']}")
            print()
        return False
    else:
        print("✅ No hardcoded credentials found")
    
    # Check .env file
    print("\n✓ Checking .env file...")
    if os.path.exists('.env'):
        print("⚠️  WARNING: .env file exists in repository root")
        print("   Make sure .env is in .gitignore")
    else:
        print("✅ .env file not in repository root")
    
    # Check .gitignore
    print("\n✓ Checking .gitignore...")
    if os.path.exists('.gitignore'):
        with open('.gitignore', 'r') as f:
            gitignore = f.read()
            if '.env' in gitignore:
                print("✅ .env is in .gitignore")
            else:
                print("❌ .env is NOT in .gitignore")
                return False
    else:
        print("❌ .gitignore file not found")
        return False
    
    print("\n" + "=" * 60)
    print("✅ All security checks passed!")
    print("=" * 60)
    print("\nReminder:")
    print("• Never commit .env files")
    print("• Use environment variables for all sensitive data")
    print("• Review changes before pushing: git diff")
    print("• See SECURITY.md for detailed guidelines")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
