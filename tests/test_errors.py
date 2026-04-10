import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from errors import LocalUnavailableError

def test_local_unavailable_inherits_runtime():
    err = LocalUnavailableError("ollama not running")
    assert isinstance(err, RuntimeError)
    assert str(err) == "ollama not running"
