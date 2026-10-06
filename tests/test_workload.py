import random

import pytest

from bench.workload import LengthDist, Workload


@pytest.mark.parametrize(
    ("spec", "kind", "params"),
    [
        ("fixed:128", "fixed", (128.0,)),
        ("uniform:10,20", "uniform", (10.0, 20.0)),
        ("normal:300,50", "normal", (300.0, 50.0)),
        ("normal:300,50,100,400", "normal", (300.0, 50.0, 100.0, 400.0)),
        (" Uniform :1,1", "uniform", (1.0, 1.0)),
    ],
)
def test_parse(spec, kind, params):
    dist = LengthDist.parse(spec)
    assert (dist.kind, dist.params) == (kind, params)


@pytest.mark.parametrize(
    "spec",
    ["", "fixed", "fixed:0", "fixed:abc", "uniform:5,1", "uniform:0,4", "normal:1", "poisson:3"],
)
def test_parse_rejects(spec):
    with pytest.raises(ValueError):
        LengthDist.parse(spec)


def test_samples_stay_in_bounds():
    rng = random.Random(0)
    uniform = LengthDist.parse("uniform:10,20")
    normal = LengthDist.parse("normal:50,40,30,60")
    assert {uniform.sample(rng) for _ in range(2000)} == set(range(10, 21))
    assert all(30 <= normal.sample(rng) <= 60 for _ in range(2000))
    assert all(LengthDist.parse("normal:2,10").sample(rng) >= 1 for _ in range(2000))
    assert LengthDist.parse("fixed:7").sample(rng) == 7


def make(seed=0, prompt="uniform:30,300", output="uniform:16,64"):
    return Workload(LengthDist.parse(prompt), LengthDist.parse(output), seed)


def test_requests_are_a_pure_function_of_seed_and_index():
    a, b = make(), make()
    assert [a.request(i) for i in range(50)] == [b.request(i) for i in range(50)]
    assert a.request(5) == a.request(5)
    assert make(seed=1).request(0).prompt != a.request(0).prompt


def test_prompt_has_exactly_the_requested_word_count():
    w = make()
    for i in range(200):
        spec = w.request(i)
        assert len(spec.prompt.split()) == spec.prompt_words
        assert 30 <= spec.prompt_words <= 300
        assert 16 <= spec.max_tokens <= 64


def test_short_prompts_fall_back_to_the_template_minimum():
    spec = make(prompt="fixed:1").request(0)
    assert spec.prompt_words == len(spec.prompt.split()) > 1


def test_no_two_prompts_share_a_first_word():
    w = make()
    first_words = [w.request(i).prompt.split()[0] for i in range(2000)]
    assert len(set(first_words)) == len(first_words)


def test_fingerprint_identifies_the_workload():
    assert make().fingerprint() == make().fingerprint()
    assert make().fingerprint() != make(seed=1).fingerprint()
    assert make().fingerprint() != make(output="fixed:64").fingerprint()
