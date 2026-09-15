"""Pytest configuration: make ``app`` importable from the backend directory.

This conftest ensures the backend package is on sys.path so that tests can
import ``app.*`` modules regardless of the working directory.
"""
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)