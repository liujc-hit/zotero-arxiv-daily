"""Package-import registration of retriever plugins in a clean process."""

import subprocess
import sys
from typing import Final

_REGISTRY_SNAPSHOT: Final = (
    "import zotero_arxiv_daily.retriever; "
    "from zotero_arxiv_daily.retriever.base import registered_retrievers; "
    "print('\\n'.join(sorted(registered_retrievers)))"
)


def test_clean_package_import_registers_pubmed() -> None:
    # Given a fresh interpreter that imports only the retriever package,
    # so direct module imports from other tests cannot mask registration.
    completed = subprocess.run(
        [sys.executable, "-c", _REGISTRY_SNAPSHOT],
        capture_output=True,
        text=True,
        check=True,
    )

    # When the registered retriever names are read back from that process.
    names = frozenset(completed.stdout.split())

    # Then pubmed is registered by the package import alone.
    assert "pubmed" in names
