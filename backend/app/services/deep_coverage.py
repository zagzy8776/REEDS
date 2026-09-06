"""Compatibility re-export for the real fixture coverage engine.

The implementation lives in app.scraper.deep_coverage. This module keeps older
service imports working so a Render deployment cannot fail during startup when
scheduler.py imports the historical service path.
"""

from app.scraper.deep_coverage import purge_showcase_rows, run_deep_coverage

__all__ = ["purge_showcase_rows", "run_deep_coverage"]
