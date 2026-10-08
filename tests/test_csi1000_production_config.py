import unittest

from csi1000_production_config import (
    BASELINE_PROFILE,
    MODEL_NUM_THREADS,
    STAGE_B_RESULT_COMMIT,
    STAGE_B_SNAPSHOT_TOKEN,
    WINNER_PROFILE,
    apply_csi1000_model_profile,
    profile_manifest,
)


def _base_cfg():
    return {
        "task": {
            "model": {
                "class": "LGBModel",
                "module_path": "qlib.contrib.model.gbdt",
                "kwargs": {
                    "loss": "mse",
                    "colsample_bytree": 0.9,
                    "learning_rate": 0.1,
                    "subsample": 0.9,
                    "lambda_l1": 205.6999,
                    "lambda_l2": 580.9768,
                    "max_depth": 8,
                    "num_leaves": 250,
                    "num_threads": 8,
                },
            }
        }
    }


class CSI1000ProductionConfigTests(unittest.TestCase):
    def test_winner_manifest_is_frozen_stage_b_rank1(self):
        row = profile_manifest(WINNER_PROFILE)
        self.assertEqual(
            row["candidate_id"],
            "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
        )
        self.assertEqual(row["stage_a_rank"], 5)
        self.assertEqual(row["lightgbm_num_threads"], 20)
        self.assertEqual(
            row["model_config_sha256"],
            "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
        )
        self.assertEqual(STAGE_B_RESULT_COMMIT, "7a2676397b0f8e6f69c0bffc98d1764647f644ac")
        self.assertTrue(STAGE_B_SNAPSHOT_TOKEN.startswith("51756897fc752304"))

    def test_winner_profile_reproduces_stage_b_model_hash(self):
        cfg = _base_cfg()
        manifest = apply_csi1000_model_profile(cfg, WINNER_PROFILE)
        kwargs = cfg["task"]["model"]["kwargs"]
        self.assertEqual(kwargs["num_threads"], MODEL_NUM_THREADS)
        self.assertEqual(kwargs["learning_rate"], 0.08)
        self.assertEqual(kwargs["colsample_bytree"], 0.8)
        self.assertEqual(kwargs["lambda_l1"], 10.0)
        self.assertEqual(kwargs["lambda_l2"], 400.0)
        self.assertEqual(kwargs["max_depth"], 6)
        self.assertEqual(kwargs["num_leaves"], 63)
        self.assertEqual(kwargs["min_data_in_leaf"], 50)
        self.assertTrue(kwargs["deterministic"])
        self.assertTrue(kwargs["force_col_wise"])
        self.assertEqual(
            manifest["model_config_sha256"],
            "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
        )

    def test_baseline_profile_reproduces_stage_b_control_hash(self):
        cfg = _base_cfg()
        manifest = apply_csi1000_model_profile(cfg, BASELINE_PROFILE)
        kwargs = cfg["task"]["model"]["kwargs"]
        self.assertEqual(kwargs["num_threads"], 20)
        self.assertEqual(kwargs["min_data_in_leaf"], 20)
        self.assertEqual(
            manifest["model_config_sha256"],
            "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
        )

    def test_profile_fails_closed_on_non_stage_b_model_shape(self):
        cfg = _base_cfg()
        cfg["task"]["model"]["module_path"] = "unexpected.module"
        with self.assertRaises(RuntimeError):
            apply_csi1000_model_profile(cfg, WINNER_PROFILE)

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            profile_manifest("retune_after_seeing_forward")


if __name__ == "__main__":
    unittest.main()
