"""Wording rules: the sample in the third person, and moods read only from real feelings."""
from thoughtcoach import coach, tone
from thoughtcoach.insights import _phrase


def test_third_person_uses_him_for_you_as_an_object():
    assert coach.third("the engine had you at 43%.") == "the engine had him at 43%."
    assert coach.third("Your next 3 moves took you from 49% to 39%") == "His next 3 moves took him from 49% to 39%"
    assert coach.third("It cost you 10%, before you moved") == "It cost him 10%, before he moved"
    assert coach.third("Nothing you said showed you’d seen it.") == "Nothing he said showed he'd seen it."
    assert coach.third("Being harsh with yourself") == "Being harsh with himself"


def test_third_person_page_leaves_advice_and_scripts_alone():
    page = ("<p class='habit'>Before you move, ask what <b>your</b> opponent wants.</p>"
            "<p title='you drew'>You lost</p><script>you</script>")
    assert coach.third_html(page) == ("<p class='habit'>Before you move, ask what <b>your</b> opponent wants.</p>"
                                      "<p title='he drew'>He lost</p><script>you</script>")


def test_mood_words_describing_the_board_are_not_feelings():
    assert tone.score("a good portion of the center and I should take it")[0] == 0
    assert tone.score("the bad bishop")[0] == 0
    assert tone.score("I have a good position")[0] == 1
    assert tone.score("this is bad")[0] == -1


def test_mm_hmm_is_not_doubt_and_playing_faster_is_about_the_clock():
    assert _phrase("Mm -hmm Yeah, it makes sense") is None
    assert _phrase("I should have played faster")[0] == "clock"
    assert _phrase("I'm not sure about this")[0] == "unsure"
