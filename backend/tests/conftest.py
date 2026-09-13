"""Pytest configuration: make ``app`` importable from the backend directory."""
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Migration scripts live in the repo-root scripts/ folder (git-ignored from
# backend); add it so `import migration_common` etc. work in tests without
# requiring live Aiven/Cockroach (the migration source is only ever scratch).
SCRIPTS_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)