from thoughtcoach import donate


def test_no_ask_without_a_link_or_before_five_games():
    assert donate.card(25, url="") == ""
    assert donate.card(4, url="https://buy.stripe.com/x") == ""


def test_ask_from_five_games():
    c = donate.card(5, url="https://buy.stripe.com/x")
    assert "5 games reviewed" in c and "__prefilled_amount=500" in c and "__prefilled_amount=1000" in c
    assert "Not now" in c and " hidden>" in c  # shown by the script, so a dismissed ask never flashes
