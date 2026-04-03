# tests/conftest.py
import pytest
import os

# Ensure test environment variables
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_trading.db")
os.environ.setdefault("SECRET_KEY",   "test-only-secret-key-abc123xyz")
os.environ.setdefault("DEBUG",        "true")
