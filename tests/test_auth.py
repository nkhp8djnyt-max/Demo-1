import time

import pytest

from app.auth import InitDataError, make_test_init_data, sign_init_data, validate_init_data
from devtools.fakes import BOT_TOKEN


def test_valid_init_data():
    user = validate_init_data(make_test_init_data(BOT_TOKEN, 42, "Mia"), BOT_TOKEN)
    assert user.id == 42 and user.first_name == "Mia"


def test_wrong_bot_token_rejected():
    with pytest.raises(InitDataError, match="bad signature"):
        validate_init_data(make_test_init_data("999:other", 42), BOT_TOKEN)


def test_tampered_user_rejected():
    data = make_test_init_data(BOT_TOKEN, 42).replace("%3A42%2C", "%3A43%2C")
    assert "43" in data
    with pytest.raises(InitDataError, match="bad signature"):
        validate_init_data(data, BOT_TOKEN)


def test_expired_rejected():
    data = make_test_init_data(BOT_TOKEN, 42, auth_date=int(time.time()) - 2 * 86400)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(data, BOT_TOKEN)


@pytest.mark.parametrize("data", ["", "garbage", "user=%7B%7D&auth_date=1"])
def test_malformed_rejected(data):
    with pytest.raises(InitDataError):
        validate_init_data(data, BOT_TOKEN)


def test_missing_user_rejected():
    data = sign_init_data({"auth_date": str(int(time.time()))}, BOT_TOKEN)
    with pytest.raises(InitDataError, match="missing user"):
        validate_init_data(data, BOT_TOKEN)
