"""Property tests for the main entry point."""

from importlib.metadata import version
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

import closing_signal

# Import once to avoid repeated imports inside the @given loop.
import main as main_module


def test_runtime_version_matches_distribution_metadata() -> None:
    assert closing_signal.__version__ == version("closing-signal")


# Property: main errors terminate with a nonzero code.
@given(error_msg=st.text(min_size=1, max_size=100))
@h_settings(max_examples=30, deadline=None)
def test_main_exits_nonzero_on_exception(error_msg: str) -> None:
    """Property 13: any unhandled main() error results in sys.exit(1)."""
    with patch("closing_signal.cli.run", side_effect=RuntimeError(error_msg)):
        with pytest.raises(SystemExit) as exc_info:
            main_module.main()
        assert exc_info.value.code != 0
