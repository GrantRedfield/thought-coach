from thoughtcoach import donate


def test_no_ask_without_a_link_or_before_ten_games():
    assert donate.card(25, url="") == ""
    assert donate.card(9, url="https://buy.stripe.com/x") == ""


def test_ask_after_ten_games():
    c = donate.card(10, url="https://buy.stripe.com/x")
    assert "10 games reviewed" in c and "__prefilled_amount=500" in c and "__prefilled_amount=1000" in c
    assert "Not now" in c and " hidden>" in c  # shown by the script, so a dismissed ask never flashes
