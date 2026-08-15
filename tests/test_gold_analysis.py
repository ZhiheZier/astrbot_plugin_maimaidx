import statistics
import unittest
from pathlib import Path

from ..libraries.maimaidx_analysis import (
    analysis_card_asset,
    analysis_type_badge_asset,
    analyze_b50_gold,
    analyze_b50_water,
)
from ..libraries.maimaidx_play_result import Best50, PlayedResult


def make_record(song_id: int, rating: int, level_value: float = 13.0) -> PlayedResult:
    return PlayedResult(
        song_id=song_id,
        song_name=f'song-{song_id}',
        level='13',
        level_index=3,
        level_value=level_value,
        type='DX',
        rating=rating,
        achievements=100.0,
        rate='sss',
        fc='fc',
    )


class GoldAnalysisTest(unittest.TestCase):
    def test_uses_native_card_asset_for_each_difficulty(self):
        expected_names = (
            'UI_TST_MBase_BSC.png',
            'UI_TST_MBase_ADV.png',
            'UI_TST_MBase_EXP.png',
            'UI_TST_MBase_MST.png',
            'UI_TST_MBase_MST_Re.png',
        )

        for level_index, expected_name in enumerate(expected_names):
            asset: Path = analysis_card_asset(level_index)
            self.assertEqual(asset.name, expected_name)
            self.assertTrue(asset.is_file())

    def test_uses_native_type_badges_without_stretching_static_assets(self):
        self.assertEqual(
            analysis_type_badge_asset('SD').name,
            'UI_UPE_Infoicon_StandardMode.png',
        )
        self.assertEqual(
            analysis_type_badge_asset('DX').name,
            'UI_UPE_Infoicon_DeluxeMode.png',
        )
        self.assertTrue(analysis_type_badge_asset('SD').is_file())
        self.assertTrue(analysis_type_badge_asset('DX').is_file())

    def test_sorts_current_b50_by_fitted_minus_official_and_caps_at_ten(self):
        records = [make_record(song_id, song_id) for song_id in range(1, 16)]
        fitted = {record.song_id: 13.0 + record.song_id / 100 for record in records}
        best50 = Best50(sd=records[:10], dx=records[10:])

        analysis = analyze_b50_gold(best50, lambda record: fitted[record.song_id])

        self.assertEqual(len(analysis.top_charts), 10)
        self.assertEqual(
            [chart.record.song_id for chart in analysis.top_charts],
            list(range(15, 5, -1)),
        )
        self.assertAlmostEqual(analysis.maximum, 0.15)
        self.assertAlmostEqual(analysis.top_charts[0].delta, analysis.maximum)

    def test_sorts_water_by_official_minus_fitted_and_caps_at_ten(self):
        records = [make_record(song_id, song_id) for song_id in range(1, 16)]
        fitted = {record.song_id: 13.0 - record.song_id / 100 for record in records}
        best50 = Best50(sd=records[:10], dx=records[10:])

        analysis = analyze_b50_water(
            best50,
            lambda record: fitted[record.song_id],
        )

        self.assertEqual(len(analysis.top_charts), 10)
        self.assertEqual(
            [chart.record.song_id for chart in analysis.top_charts],
            list(range(15, 5, -1)),
        )
        self.assertAlmostEqual(analysis.maximum, 0.15)
        self.assertAlmostEqual(analysis.top_charts[0].delta, analysis.maximum)

    def test_calculates_statistics_from_all_valid_b50_records(self):
        records = [
            make_record(1, 100, 13.0),
            make_record(2, 200, 13.0),
            make_record(3, 300, 13.0),
            make_record(4, 400, 13.0),
        ]
        fitted = {1: 13.5, 2: 13.2, 3: 12.9, 4: 12.6}
        values = [0.5, 0.2, -0.1, -0.4]

        analysis = analyze_b50_gold(
            Best50(sd=records), lambda record: fitted[record.song_id]
        )

        self.assertEqual(analysis.total_count, 4)
        self.assertEqual(analysis.valid_count, 4)
        self.assertAlmostEqual(analysis.mean, statistics.mean(values))
        self.assertAlmostEqual(analysis.maximum, max(values))
        self.assertAlmostEqual(analysis.minimum, min(values))
        self.assertAlmostEqual(analysis.median, statistics.median(values))
        self.assertAlmostEqual(analysis.std_dev, statistics.pstdev(values))

    def test_excludes_missing_fitted_values_and_keeps_original_records(self):
        included = make_record(1, 100, 13.0)
        missing = make_record(2, 200, 14.0)
        original_dump = included.model_dump()

        analysis = analyze_b50_gold(
            Best50(sd=[included, missing]),
            lambda record: 13.4 if record.song_id == 1 else None,
        )

        self.assertEqual(analysis.total_count, 2)
        self.assertEqual(analysis.valid_count, 1)
        self.assertEqual([chart.record.song_id for chart in analysis.top_charts], [1])
        self.assertAlmostEqual(analysis.std_dev, 0.0)
        self.assertEqual(included.model_dump(), original_dump)

    def test_returns_empty_statistics_when_no_fitted_values_exist(self):
        analysis = analyze_b50_gold(
            Best50(sd=[make_record(1, 100)]), lambda _record: None
        )

        self.assertEqual(analysis.valid_count, 0)
        self.assertEqual(analysis.top_charts, [])
        self.assertEqual(analysis.mean, 0)


if __name__ == '__main__':
    unittest.main()
