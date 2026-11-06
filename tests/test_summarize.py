"""Offline tests for the summary extractor.

The fixtures copy the shape of real Garmin responses, with made-up
values. Field shapes are the contract here: Garmin nests the sleep
score, returns weights in grams, and puts stress durations in
seconds. These tests pin those three facts down.
"""

import unittest
from datetime import date

from garmin_daily.summarize import (
    normalize_weigh_ins,
    summarize_day,
    summarize_week,
)


def raw_day():
    return {
        "summary": {
            "totalSteps": 10000, "dailyStepGoal": 8000,
            "totalDistanceMeters": 8000.0, "floorsAscended": 10,
            "totalKilocalories": 2600.0, "activeKilocalories": 500.0,
            "bmrKilocalories": 2100.0, "restingHeartRate": 62,
            "minHeartRate": 48, "maxHeartRate": 140,
            "lastSevenDaysAvgRestingHeartRate": 63,
            "averageStressLevel": 40, "maxStressLevel": 90,
            "stressQualifier": "BALANCED",
            "restStressDuration": 3600 * 5,
            "lowStressDuration": 3600 * 8,
            "mediumStressDuration": 3600 * 3,
            "highStressDuration": 3600 * 1,
            "bodyBatteryAtWakeTime": 70, "bodyBatteryHighestValue": 85,
            "bodyBatteryLowestValue": 12, "bodyBatteryMostRecentValue": 20,
            "bodyBatteryChargedValue": 60, "bodyBatteryDrainedValue": 55,
            "averageSpo2": 97, "lowestSpo2": 91,
            "avgWakingRespirationValue": 14.0,
            "moderateIntensityMinutes": 30, "vigorousIntensityMinutes": 10,
            "intensityMinutesGoal": 150,
        },
        "sleep": {
            "avgOvernightHrv": 28,
            "dailySleepDTO": {
                "calendarDate": "2026-11-05",
                "sleepTimeSeconds": 25200, "deepSleepSeconds": 3600,
                "remSleepSeconds": 5400, "lightSleepSeconds": 14400,
                "awakeSleepSeconds": 1800,
                "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
                "averageRespirationValue": 13.5, "avgSleepStress": 18.0,
            },
        },
        "stress": {"avgStressLevel": 40, "maxStressLevel": 90},
        "spo2": {"averageSpO2": 97, "lowestSpO2": 91, "latestSpO2": 98},
        "respiration": {"avgWakingRespirationValue": 14.0, "avgSleepRespirationValue": 13.0,
                        "highestRespirationValue": 20.0, "lowestRespirationValue": 9.0},
        "hrv": {"hrvSummary": {"lastNightAvg": 28, "weeklyAvg": 27, "status": "BALANCED",
                               "baseline": {"balancedLow": 23, "balancedUpper": 33}}},
        "training_readiness": [{"calendarDate": "2026-11-05", "score": 75,
                                "level": "HIGH", "sleepScore": 82, "feedbackShort": "GO"}],
    }


class SummarizeDayTests(unittest.TestCase):
    def test_headline_numbers(self):
        s = summarize_day(date(2026, 11, 5), raw_day(), [], [])
        self.assertTrue(s["complete"])
        self.assertEqual(s["steps"]["total"], 10000)
        self.assertEqual(s["sleep"]["score"], 82)
        self.assertEqual(s["sleep"]["duration_min"], 420)
        self.assertEqual(s["sleep"]["deep_min"], 60)
        self.assertEqual(s["stress"]["avg"], 40)
        self.assertEqual(s["stress"]["rest_min"], 300)
        self.assertEqual(s["stress"]["high_min"], 60)
        self.assertEqual(s["hrv"]["last_night_avg_ms"], 28)
        self.assertEqual(s["hrv"]["status"], "BALANCED")
        self.assertEqual(s["body_battery"]["highest"], 85)
        self.assertEqual(s["training_readiness"]["score"], 75)

    def test_missing_sleep_is_incomplete_not_zero(self):
        raw = raw_day()
        raw["sleep"] = None
        s = summarize_day(date(2026, 11, 5), raw, [], [])
        self.assertFalse(s["complete"])
        self.assertIsNone(s["sleep"]["score"])

    def test_activity_summary_drops_noise(self):
        act = {"activityId": 1, "activityName": "Walk", "activityType": {"typeKey": "walking"},
               "startTimeLocal": "2026-11-05 07:00:00", "duration": 3600.0, "distance": 5000.0,
               "averageHR": 100, "maxHR": 120, "calories": 300, "steps": 6000,
               "ownerProfileImageUrlLarge": "https://example.invalid/x.png"}
        s = summarize_day(date(2026, 11, 5), raw_day(), [act], [])
        self.assertEqual(len(s["activities"]), 1)
        self.assertEqual(s["activities"][0]["duration_min"], 60.0)
        self.assertNotIn("ownerProfileImageUrlLarge", str(s["activities"][0]))


class WeighInTests(unittest.TestCase):
    def test_grams_become_pounds(self):
        raw = {"dailyWeightSummaries": [{
            "summaryDate": "2026-11-01",
            "allWeightMetrics": [{"calendarDate": "2026-11-01", "date": 1, "timestampGMT": 2,
                                  "weight": 80000.0, "bmi": 22.6, "bodyFat": 18.0,
                                  "bodyWater": 59.0, "boneMass": 5000, "muscleMass": 33000,
                                  "sourceType": "INDEX_SCALE"}],
            "latestWeight": None,
        }]}
        by_date = normalize_weigh_ins(raw)
        reading = by_date["2026-11-01"][0]
        self.assertEqual(reading["weight_kg"], 80.0)
        self.assertEqual(reading["weight_lb"], 176.4)
        self.assertEqual(reading["muscle_mass_lb"], 72.8)


class WeekTests(unittest.TestCase):
    def test_averages_skip_gaps(self):
        days = []
        for i, steps in enumerate((10000, None, 8000)):
            raw = raw_day()
            if steps is None:
                raw["summary"]["totalSteps"] = None
            else:
                raw["summary"]["totalSteps"] = steps
            days.append(summarize_day(date(2026, 11, 3 + i), raw, [], []))
        rollup = summarize_week(days, {})
        self.assertEqual(rollup["steps_avg"], 9000.0)
        self.assertEqual(rollup["days_with_data"], 3)


if __name__ == "__main__":
    unittest.main()
