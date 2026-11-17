import os
import sys
import pytest

# Make the repo root importable so tests can import the app modules directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests always run the app in self-host (BYOK) mode. app.py freezes
# BILLING_ENABLED at import time and load_dotenv never overrides an existing
# variable, so this must be set here — before any test module imports app — or
# the suite's behavior would depend on the developer's personal .env.
os.environ["BILLING_ENABLED"] = "0"


@pytest.fixture(autouse=True)
def isolated_local_campaigns(tmp_path, monkeypatch):
    """Endpoint tests must not add labels to the developer's real library."""
    module = sys.modules.get("app")
    if module is not None and hasattr(module, "LOCAL_CAMPAIGNS"):
        from campaigns import Campaigns
        monkeypatch.setattr(module, "LOCAL_CAMPAIGNS", Campaigns(tmp_path / "campaigns.json"))
