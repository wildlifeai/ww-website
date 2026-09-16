# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Species Brain training domain — the pure dataset rules."""

import io
import json
import zipfile

import pytest

from app.domain.training import (
    BACKGROUND_ROLE,
    TARGET_ROLE,
    DatasetSummary,
    TrainingError,
    assign_samples,
    build_dataset_zip,
    build_label_map,
    default_background_label,
    firmware_target_warning,
    label_slug,
    sanitize_label,
    split_samples,
    validate_dataset,
)


def _obs(oid, name, *, otype="animal", crop=None, origin="cloud", human=False):
    return {
        "id": oid,
        "observation_type": otype,
        "scientific_name": name,
        "crop_url": crop,
        "ai_origin": origin,
        "source_type": "human" if human else "ai",
        "reviewer_id": "u1" if human else None,
        "annotator_id": None,
    }


def _media(mid, observations, *, animal_crop=None, file_path=None):
    return {
        "id": mid,
        "deployment_id": "dep-1",
        "file_path": file_path or f"gdrive://{mid}",
        "media_assets": {"animal_crop_url": animal_crop},
        "observations": observations,
    }


RAT = {"label": "rat", "scientific_name": "Rattus rattus", "taxon_id": "t-rat", "vernacular_name": "Ship rat"}


class TestSanitizeLabel:
    def test_keeps_readable_labels(self):
        assert sanitize_label("Rat") == "rat"
        assert sanitize_label("not rat") == "not rat"
        assert sanitize_label("Ship  rat!") == "ship rat"

    def test_truncates_and_trims(self):
        assert len(sanitize_label("x" * 50)) == 32
        assert sanitize_label("__rat--") == "rat"

    def test_slug_has_no_spaces(self):
        assert label_slug("not rat") == "not_rat"


class TestDefaultBackgroundLabel:
    def test_not_target_when_it_sorts_first(self):
        assert default_background_label(["rat"]) == "not rat"

    def test_falls_back_when_not_prefix_sorts_after_target(self):
        # "not gecko" > "gecko" alphabetically, which would make the negative class index 1.
        assert default_background_label(["gecko"]) == "background"

    def test_multi_class_prefers_other(self):
        assert default_background_label(["rat", "stoat"]) == "other"

    def test_underscore_when_everything_sorts_after(self):
        assert default_background_label(["a"]) == "_background"


class TestAssignSamples:
    def test_human_beats_cloud_and_edge_is_ignored(self):
        rows = [
            _media(
                "m1",
                [
                    _obs("o-cloud", "Mus musculus", crop="https://x/cloud.jpg"),
                    _obs("o-human", "Rattus rattus", crop="https://x/human.jpg", human=True),
                    _obs("o-edge", "Rattus rattus", origin="edge"),
                ],
            )
        ]
        samples, summary = assign_samples(rows, [RAT], include_background=True, background_label="")
        assert [s.sample_id for s in samples] == ["o-human"]
        assert samples[0].label == "rat" and samples[0].role == TARGET_ROLE
        assert samples[0].image_ref == "https://x/human.jpg" and samples[0].is_full_frame is False
        assert summary.labels == ["not rat", "rat"]
        assert summary.counts == {"not rat": 0, "rat": 1}

    def test_blank_and_unlisted_go_to_background(self):
        rows = [
            _media("m1", [_obs("o1", None, otype="blank")]),
            _media("m2", [_obs("o2", "Felis catus", crop="https://x/cat.jpg")]),
            _media("m3", [_obs("o3", "Rattus rattus")], animal_crop="https://x/animal.jpg"),
        ]
        samples, summary = assign_samples(rows, [RAT], include_background=True, background_label="")
        by_id = {s.sample_id: s for s in samples}
        assert by_id["m1:blank"].role == BACKGROUND_ROLE and by_id["m1:blank"].is_full_frame
        assert by_id["o2"].role == BACKGROUND_ROLE
        assert by_id["o3"].image_ref == "https://x/animal.jpg"  # animal crop when the observation has none
        assert summary.counts == {"not rat": 2, "rat": 1}

    def test_background_off_skips_unlisted_and_counts_them(self):
        rows = [_media("m2", [_obs("o2", "Felis catus")]), _media("m1", [_obs("o1", None, otype="blank")])]
        samples, summary = assign_samples(rows, [RAT], include_background=False, background_label="")
        assert samples == []
        assert summary.labels == ["rat"]
        assert summary.skipped_unlisted == 1

    def test_edge_only_and_unlabelled_media_are_reported(self):
        rows = [_media("m1", [_obs("o1", "Rattus rattus", origin="edge")]), _media("m2", [])]
        _, summary = assign_samples(rows, [RAT], include_background=True, background_label="")
        assert summary.edge_only_ignored == 1
        assert summary.skipped_unlabelled == 1

    def test_custom_background_label_is_sanitised(self):
        rows = [_media("m1", [_obs("o1", None, otype="blank")])]
        _, summary = assign_samples(rows, [RAT], include_background=True, background_label="  Nothing Here! ")
        assert summary.labels == ["nothing here", "rat"]


class TestValidateDataset:
    def _summary(self, counts):
        return DatasetSummary(labels=list(counts), counts=dict(counts))

    def test_needs_two_populated_classes(self):
        with pytest.raises(TrainingError, match="at least two classes"):
            validate_dataset(self._summary({"not rat": 0, "rat": 30}), min_per_class=20, max_images=100, max_classes=16)

    def test_min_per_class(self):
        with pytest.raises(TrainingError, match="too few for: not rat \\(5\\)"):
            validate_dataset(self._summary({"not rat": 5, "rat": 30}), min_per_class=20, max_images=100, max_classes=16)

    def test_max_images_and_classes(self):
        with pytest.raises(TrainingError, match="limit"):
            validate_dataset(self._summary({"a": 60, "b": 60}), min_per_class=20, max_images=100, max_classes=16)
        with pytest.raises(TrainingError, match="at most 2"):
            validate_dataset(self._summary({"a": 60, "b": 60, "c": 60}), min_per_class=20, max_images=1000, max_classes=2)

    def test_ok(self):
        validate_dataset(self._summary({"not rat": 20, "rat": 25}), min_per_class=20, max_images=100, max_classes=16)


class TestSplitSamples:
    def _samples(self, n, label):
        from app.domain.training import TrainingSample

        return [TrainingSample(f"{label}-{i}", f"m{i}", None, label, TARGET_ROLE, "x", False) for i in range(n)]

    def test_deterministic_and_roughly_20_percent(self):
        samples = self._samples(500, "rat")
        a = split_samples(samples)
        b = split_samples(samples)
        assert [s.sample_id for s in a[1]] == [s.sample_id for s in b[1]]
        assert 60 <= len(a[1]) <= 140  # 20% ± noise on 500 hashed ids

    def test_every_class_keeps_a_training_sample(self):
        samples = self._samples(1, "lonely")
        train, test = split_samples(samples, test_fraction=1.0)
        assert len(train) == 1 and test == []


class TestDatasetZipAndLabelMap:
    def test_zip_layout_and_manifest(self):
        from app.domain.training import TrainingSample

        s1 = TrainingSample("o1", "m1", None, "not rat", BACKGROUND_ROLE, "x", True)
        s2 = TrainingSample("o2", "m2", None, "rat", TARGET_ROLE, "x", False)
        summary = DatasetSummary(labels=["not rat", "rat"], counts={"not rat": 1, "rat": 1})
        data = build_dataset_zip([(s1, b"a"), (s2, b"b")], [], summary, model_name="Rat ID", request={"epochs": 30})
        names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
        assert names == ["README.txt", "dataset.json", "training/not_rat/not_rat.0001.jpg", "training/rat/rat.0001.jpg"]
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("dataset.json"))
        assert manifest["classes"] == [{"label": "not rat", "count": 1}, {"label": "rat", "count": 1}]

    def test_label_map_targets_carry_taxon_and_background_is_marked(self):
        m = build_label_map([RAT], ["not rat", "rat"], "not rat")
        assert m["rat"] == {"role": "target", "taxon_id": "t-rat", "scientific_name": "Rattus rattus", "vernacular_name": "Ship rat"}
        assert m["not rat"]["role"] == BACKGROUND_ROLE

    def test_firmware_warning_only_when_background_is_index_1(self):
        m = build_label_map([{"label": "gecko", "scientific_name": "x"}], ["gecko", "not gecko"], "not gecko")
        assert "index 1" in firmware_target_warning(["gecko", "not gecko"], m)
        m2 = build_label_map([RAT], ["not rat", "rat"], "not rat")
        assert firmware_target_warning(["not rat", "rat"], m2) is None
