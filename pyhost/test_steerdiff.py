"""Workspace steering diff: the comparison arithmetic (docs/STEERING_DIFF.md, gate G5). CPU only, synthetic records.
Run: pixi run --no-install python3 pyhost/test_steerdiff.py"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steerdiff as sd  # noqa: E402

SETTINGS = {"emotion": {"layers": [20, 24], "names": ["joyful", "sad"]}, "workspace": {"layers": [36, 40], "k": 4}}
IDENTITY = {"library_sha256": "c660", "build_id": "0x1", "directions": "b3cd", "lens": "ef6d", "tokenizer": "c9db"}


def rec(pos, tok, source="generated", emo=None, ws=None):
    """A nomos.readout/1 record. emo: [[joyful, sad] per emotion layer]; ws: [[[id, score] x k] per workspace layer]."""
    return {"pos": pos, "token": tok, "source": source, "channel": None if source == "prompt" else "answer",
            "emotion": None if emo is None else {"proj": emo, "norm": [1.0] * len(emo)},
            "workspace": None if ws is None else {"top": ws},
            "missing": [] if ws is not None or source == "prompt" else ["workspace_unavailable"]}


TOP_A = [[10, 5.0], [11, 4.0], [12, 3.0], [13, 2.0]]
TOP_B = [[11, 6.0], [14, 4.5], [10, 3.5], [15, 1.0]]


def run(run_id, tokens, mode="free", reference=None, steer=None, prompt=(7, 8, 9), ws=None, emo=None):
    P = len(prompt)
    recs = [rec(i, t, "prompt", emo=[[0.0, 0.0], [0.0, 0.0]]) for i, t in enumerate(prompt)]
    for j, t in enumerate(tokens):
        recs.append(rec(P + j, t, emo=emo or [[0.0, 0.0], [0.5, 0.0]], ws=ws or [TOP_A, TOP_A]))
    return sd.run_doc(run_id=run_id, arm={"name": run_id, "steer": steer}, mode=mode, reference=reference,
                      prompt_ids=list(prompt), tokens=list(tokens), records=recs, settings=SETTINGS, identity=IDENTITY)


class Candidates(unittest.TestCase):
    def test_union_of_the_displayed_tokens_with_exact_scores_ranks_and_bounds(self):
        got = {c["id"]: c for c in sd.workspace_candidates(TOP_A, TOP_B, display_k=2)}
        self.assertEqual(sorted(got), [10, 11, 14])       # displayed: A {10, 11}, B {11, 14}; 12, 13, 15 are not shown
        self.assertEqual(got[11], {"id": 11, "baseline": 4.0, "steered": 6.0, "baseline_rank": 2, "steered_rank": 1,
                                   "baseline_below": None, "steered_below": None, "delta": 2.0,
                                   "delta_min": None, "delta_max": None, "status": "both"})
        self.assertEqual(got[10], {"id": 10, "baseline": 5.0, "steered": 3.5, "baseline_rank": 1, "steered_rank": 3,
                                   "baseline_below": None, "steered_below": None, "delta": -1.5,
                                   "delta_min": None, "delta_max": None, "status": "left"})
        # 14 is outside the baseline's transmitted top 4: its baseline score is at most A's 4th (2.0), never 0.
        self.assertEqual(got[14], {"id": 14, "baseline": None, "steered": 4.5, "baseline_rank": None, "steered_rank": 2,
                                   "baseline_below": 2.0, "steered_below": None, "delta": None,
                                   "delta_min": 2.5, "delta_max": None, "status": "entered"})

    def test_a_token_that_left_the_transmitted_list_gets_an_upper_bound(self):
        a = [[20, 9.0], [21, 8.0], [22, 7.0], [23, 6.0]]
        b = [[21, 8.5], [22, 7.5], [23, 6.5], [24, 6.25]]
        got = {c["id"]: c for c in sd.workspace_candidates(a, b, display_k=2)}
        self.assertEqual(got[20]["steered"], None)
        self.assertEqual(got[20]["steered_below"], 6.25)
        self.assertEqual((got[20]["delta"], got[20]["delta_min"], got[20]["delta_max"]), (None, None, -2.75))
        self.assertEqual(got[20]["status"], "left")

    def test_identical_lists_have_zero_exact_deltas(self):
        for c in sd.workspace_candidates(TOP_A, copy.deepcopy(TOP_A), display_k=2):
            self.assertEqual((c["delta"], c["status"]), (0.0, "both"))

    def test_display_k_larger_than_the_transmitted_list_is_refused(self):
        with self.assertRaises(sd.SteerDiffError):
            sd.workspace_candidates(TOP_A, TOP_B, display_k=5)


class Emotion(unittest.TestCase):
    def test_emotion_deltas_are_exact_per_layer_and_direction(self):
        a = {"proj": [[0.25, -1.0], [2.0, 0.5]], "norm": [1.0, 1.0]}
        b = {"proj": [[0.75, -1.0], [1.5, 0.5]], "norm": [1.0, 1.0]}
        self.assertEqual(sd.emotion_delta(a, b), [[0.5, 0.0], [-0.5, 0.0]])

    def test_a_missing_emotion_record_has_no_delta(self):
        self.assertIsNone(sd.emotion_delta(None, {"proj": [[0.0, 0.0]], "norm": [1.0]}))


class Compare(unittest.TestCase):
    def test_matched_replay_aligns_every_position_and_names_its_sources(self):
        base = run("base", [1, 2, 3], mode="matched", reference="base")
        steered = run("joy_0.5", [1, 2, 3], mode="matched", reference="base",
                      steer={"layer": 24, "direction": "joyful", "strength": 0.5, "from_pos": 2},
                      ws=[TOP_B, TOP_A], emo=[[0.0, 0.0], [1.5, 0.0]])
        c = sd.compare(base, steered, display_k=2)
        self.assertEqual((c["schema"], c["mode"], c["reference"], c["P"]), (sd.SCHEMA, "matched", "base", 3))
        self.assertEqual((c["baseline"]["run_id"], c["steered"]["run_id"]), ("base", "joy_0.5"))
        self.assertEqual(c["steered"]["arm"]["steer"]["strength"], 0.5)
        self.assertEqual(c["baseline"]["identity"], IDENTITY)
        self.assertIsNone(c["divergence"])
        self.assertIsNone(c["tracks"])
        self.assertEqual([p["pos"] for p in c["positions"]], [0, 1, 2, 3, 4, 5])
        gen = c["positions"][3]
        self.assertEqual((gen["token"], gen["source"]), (1, "generated"))
        self.assertEqual(gen["emotion_delta"], [[0.0, 0.0], [1.0, 0.0]])
        self.assertEqual(gen["workspace"][0]["layer"], 36)
        self.assertEqual({x["id"]: x["delta"] for x in gen["workspace"][0]["candidates"]}[11], 2.0)
        self.assertEqual(gen["workspace"][1]["candidates"][0]["delta"], 0.0)
        prompt = c["positions"][0]
        self.assertEqual((prompt["source"], prompt["workspace"]), ("prompt", None))   # no prompt workspace in v1

    def test_matched_replay_of_different_tokens_is_refused(self):
        base = run("base", [1, 2, 3], mode="matched", reference="base")
        other = run("joy", [1, 2, 4], mode="matched", reference="base")
        with self.assertRaisesRegex(sd.SteerDiffError, "matched"):
            sd.compare(base, other)

    def test_matched_runs_of_different_references_are_refused(self):
        base = run("base", [1, 2, 3], mode="matched", reference="base")
        other = run("joy", [1, 2, 3], mode="matched", reference="joy_free")
        with self.assertRaisesRegex(sd.SteerDiffError, "reference"):
            sd.compare(base, other)

    def test_a_free_run_is_never_compared_as_matched(self):
        with self.assertRaisesRegex(sd.SteerDiffError, "mode"):
            sd.compare(run("base", [1, 2], mode="matched", reference="base"), run("joy", [1, 2]))

    def test_free_generation_aligns_only_up_to_the_first_differing_token(self):
        c = sd.compare(run("base", [1, 2, 3, 4]), run("joy", [1, 2, 9, 9, 9]), display_k=2)
        self.assertEqual(c["divergence"], {"index": 2, "position": 5, "baseline_token": 3, "steered_token": 9})
        self.assertEqual([p["pos"] for p in c["positions"]], [0, 1, 2, 3, 4])
        self.assertEqual(c["tracks"], {"baseline": [3, 4], "steered": [9, 9, 9]})

    def test_free_generation_where_one_reply_extends_the_other(self):
        c = sd.compare(run("base", [1, 2]), run("joy", [1, 2, 5]), display_k=2)
        self.assertEqual(c["divergence"], {"index": 2, "position": 5, "baseline_token": None, "steered_token": 5})
        self.assertEqual(c["tracks"], {"baseline": [], "steered": [5]})

    def test_different_prompts_are_refused(self):
        with self.assertRaisesRegex(sd.SteerDiffError, "prompt"):
            sd.compare(run("base", [1]), run("joy", [1], prompt=(7, 8, 6)))

    def test_different_settings_or_identities_are_refused(self):
        a, b = run("base", [1]), run("joy", [1])
        b["settings"] = copy.deepcopy(b["settings"])
        b["settings"]["workspace"]["k"] = 8
        with self.assertRaisesRegex(sd.SteerDiffError, "settings"):
            sd.compare(a, b)
        c = run("joy", [1])
        c["identity"] = dict(c["identity"], library_sha256="0000")
        with self.assertRaisesRegex(sd.SteerDiffError, "identity"):
            sd.compare(a, c)

    def test_a_missing_workspace_is_marked_never_diffed(self):
        a = run("base", [1, 2])
        b = run("joy", [1, 2])
        b["records"][3] = dict(b["records"][3], workspace=None, missing=["workspace_unavailable"])
        layer = sd.compare(a, b, display_k=2)["positions"][3]["workspace"][0]
        self.assertEqual((layer["missing"], layer["candidates"]), (["steered"], []))


class RunDoc(unittest.TestCase):
    def test_records_must_carry_the_runs_tokens_at_their_positions(self):
        good = run("base", [1, 2])
        bad = copy.deepcopy(good["records"])
        bad[4]["token"] = 99
        with self.assertRaisesRegex(sd.SteerDiffError, "token"):
            sd.run_doc(run_id="x", arm={"name": "x", "steer": None}, mode="free", reference=None,
                       prompt_ids=good["prompt_ids"], tokens=good["tokens"], records=bad, settings=SETTINGS,
                       identity=IDENTITY)

    def test_a_matched_run_names_its_reference(self):
        with self.assertRaisesRegex(sd.SteerDiffError, "reference"):
            run("joy", [1], mode="matched", reference=None)


class Summaries(unittest.TestCase):
    def comparison(self):
        base = run("base", [1, 2, 3], mode="matched", reference="base")
        steered = run("joy", [1, 2, 3], mode="matched", reference="base", ws=[TOP_B, TOP_A],
                      emo=[[0.0, 0.0], [1.5, -0.5]])
        steered["records"][4] = dict(steered["records"][4], workspace={"top": [TOP_A, TOP_A]})   # one position unchanged
        return sd.compare(base, steered, display_k=2)

    def test_movers_average_exact_deltas_and_count_entries_per_layer(self):
        got = {m["id"]: m for m in sd.movers(self.comparison(), 0)}
        # layer 36: positions 3 and 5 move 11 by +2.0 and 10 by -1.5; position 4 is unchanged (0.0 for 10 and 11)
        self.assertEqual(got[11], {"id": 11, "mean_delta": 4.0 / 3, "count": 3, "entered": 0, "left": 0})
        self.assertEqual(got[10], {"id": 10, "mean_delta": -1.0, "count": 3, "entered": 0, "left": 2})
        # 14 has no exact delta anywhere (its baseline score is unknown), so no mean; it entered the shown list twice
        self.assertEqual(got[14], {"id": 14, "mean_delta": None, "count": 0, "entered": 2, "left": 0})

    def test_movers_sort_by_mean_delta_with_unknown_means_last(self):
        self.assertEqual([m["id"] for m in sd.movers(self.comparison(), 0)], [11, 10, 14])

    def test_emotion_means_over_aligned_generated_positions(self):
        self.assertEqual(sd.emotion_means(self.comparison()), [[0.0, 0.0], [1.0, -0.5]])


class Channels(unittest.TestCase):
    """The decision (the thinking tail) and the lie (the answer) are read separately."""

    def mixed(self, name, emo):
        r = run(name, [1, 2], ws=[TOP_A, TOP_B], emo=emo)
        r["records"][3] = dict(r["records"][3], channel="reasoning")
        return r

    def test_lexicon_counts_can_read_one_channel(self):
        r = self.mixed("base", None)
        self.assertEqual(sd.lexicon_counts(r, {"help": [10]}, display_k=2, channels={"reasoning"}),
                         {"positions": [1, 1], "help": [1, 0]})
        self.assertEqual(sd.lexicon_counts(r, {"help": [10]}, display_k=2, channels={"answer"})["positions"], [1, 1])

    def test_emotion_means_can_read_one_channel(self):
        a = self.mixed("base", [[0.0, 0.0], [0.0, 0.0]])
        b = self.mixed("joy", [[0.0, 0.0], [1.0, 0.0]])
        b["records"][4] = dict(b["records"][4], emotion={"proj": [[0.0, 0.0], [3.0, 0.0]], "norm": [1.0, 1.0]})
        c = sd.compare(a, b, display_k=2)
        self.assertEqual(sd.emotion_means(c, channels={"reasoning"}), [[0.0, 0.0], [1.0, 0.0]])
        self.assertEqual(sd.emotion_means(c, channels={"answer"}), [[0.0, 0.0], [3.0, 0.0]])
        self.assertIsNone(sd.emotion_means(c, channels={"tool"}))


class Lexicons(unittest.TestCase):
    def test_counts_use_the_displayed_tokens_only(self):
        r = run("base", [1, 2], ws=[TOP_A, TOP_B])                 # layer 36 shows {10, 11}; layer 40 shows {11, 14}
        got = sd.lexicon_counts(r, {"celebration": [12, 14], "help": [10]}, display_k=2)
        self.assertEqual(got, {"positions": [2, 2], "celebration": [0, 2], "help": [2, 0]})


if __name__ == "__main__":
    unittest.main()
