from thoughtcoach.bayes import interval, prob_above, prob_greater, prob_greater_prior, sceptic, span


def test_interval_and_span():
    lo, hi = interval(6, 16)
    assert 0.20 < lo < 0.23 and 0.57 < hi < 0.60
    assert span(6, 16) == "likely 21–58%"


def test_chance_one_rate_beats_another():
    assert abs(prob_greater(6, 16, 3, 11) - 0.691) < 0.005  # checked against 200,000 samples
    assert abs(prob_greater(50, 100, 50, 100) - 0.5) < 1e-6
    assert abs(prob_greater_prior(6, 16, 3, 11, (1, 1)) - prob_greater(6, 16, 3, 11)) < 0.005


def test_a_sceptical_prior_keeps_tiny_samples_modest():
    assert prob_greater(3, 3, 0, 3) > 0.95
    assert prob_greater_prior(3, 3, 0, 3, sceptic(0.5)) < prob_greater(3, 3, 0, 3)


def test_prob_above():
    assert abs(prob_above(4, 5, 0.5) - 0.890625) < 1e-6
