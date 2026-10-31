"""Keep the thumbnail-analysis cache of tests out of the real cache/analysis folder."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_analysis_cache(tmp_path_factory, monkeypatch):
    from ptah.products import curate
    monkeypatch.setattr(curate, "ANALYSIS_DIR", tmp_path_factory.mktemp("analysis"))
    curate._ANALYSIS.clear()
    curate._ANALYSIS_DIRTY.clear()
    yield
    curate._ANALYSIS.clear()
    curate._ANALYSIS_DIRTY.clear()
