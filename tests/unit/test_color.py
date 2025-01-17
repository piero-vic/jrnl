from colorama import Fore
from colorama import Style
import pytest

from jrnl.color import colorize


@pytest.fixture()
def data_fixture():
    string = "Zwei peanuts walked into a bar"
    yield string


def test_colorize(data_fixture):
    string = data_fixture
    colorized_string = colorize(string, "BLUE", True)

    assert colorized_string == Style.BRIGHT + Fore.BLUE + string + Style.RESET_ALL
