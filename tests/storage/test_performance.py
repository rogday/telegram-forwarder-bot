"""Performance benchmark tests for storage.py using pytest-benchmark."""

import pytest

from telegram_forwarder_bot.storage import KeywordGroup


class TestPerformance:
    """Performance benchmark tests using pytest-benchmark."""

    @pytest.fixture
    def small_keyword_set(self, storage_data_class):
        """Small keyword set (5 keywords)."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        data.add_keyword_group(KeywordGroup.from_lists(
            ["python", "programming"], []))
        data.add_keyword_group(
            KeywordGroup.from_lists(["test", "automation"], []))
        data.add_keyword_group(
            KeywordGroup.from_lists(["urgent", "critical"], []))
        data.add_keyword_group(
            KeywordGroup.from_lists(["error", "exception", "failure"], [])
        )
        return data

    @pytest.fixture
    def medium_keyword_set(self, storage_data_class):
        """Medium keyword set (50 keywords in 10 groups)."""
        data = storage_data_class()
        for i in range(10):
            keywords = [f"keyword{i}j{j}" for j in range(5)]
            data.add_keyword_group(KeywordGroup.from_lists(keywords, []))
        return data

    @pytest.fixture
    def large_keyword_set(self, storage_data_class):
        """Large keyword set (200 keywords in 40 groups)."""
        data = storage_data_class()
        for i in range(40):
            keywords = [f"alert{i}j{j}" for j in range(5)]
            data.add_keyword_group(KeywordGroup.from_lists(keywords, []))
        return data

    def perf_match_short_text_small_keywords(self, benchmark, small_keyword_set):
        """Benchmark: short text with small keyword set."""
        text = "hello world this is a test message"
        benchmark(small_keyword_set.matches_keywords, text)

    def perf_match_medium_text_small_keywords(self, benchmark, small_keyword_set):
        """Benchmark: medium text (~500 chars) with small keyword set."""
        text = " ".join([f"word{i}" for i in range(100)]
                        ) + " python programming here"
        benchmark(small_keyword_set.matches_keywords, text)

    def perf_match_long_text_small_keywords(self, benchmark, small_keyword_set):
        """Benchmark: long text (~5000 chars) with small keyword set."""
        text = " ".join([f"word{i}" for i in range(1000)]
                        ) + " test message here"
        benchmark(small_keyword_set.matches_keywords, text)

    def perf_match_short_text_medium_keywords(self, benchmark, medium_keyword_set):
        """Benchmark: short text with medium keyword set."""
        text = "keyword0j2 is here now"
        benchmark(medium_keyword_set.matches_keywords, text)

    def perf_match_long_text_medium_keywords(self, benchmark, medium_keyword_set):
        """Benchmark: long text with medium keyword set."""
        text = " ".join([f"word{i}" for i in range(1000)]
                        ) + " keyword5j3 found here"
        benchmark(medium_keyword_set.matches_keywords, text)

    def perf_match_short_text_large_keywords(self, benchmark, large_keyword_set):
        """Benchmark: short text with large keyword set."""
        text = "alert20j1 is the alert here"
        benchmark(large_keyword_set.matches_keywords, text)

    def perf_match_long_text_large_keywords(self, benchmark, large_keyword_set):
        """Benchmark: long text with large keyword set."""
        text = " ".join(
            [f"message{i}" for i in range(2000)]) + " alert10j4 detected"
        benchmark(large_keyword_set.matches_keywords, text)

    def perf_no_match_worst_case(self, benchmark, large_keyword_set):
        """Benchmark worst case: long text with no matches."""
        text = " ".join([f"randomword{i}" for i in range(3000)])
        benchmark(large_keyword_set.matches_keywords, text)

    def perf_add_keyword_group(self, benchmark, storage_data_class):
        """Benchmark adding a keyword group (triggers automaton rebuild)."""
        data = storage_data_class()
        # Pre-populate with many groups
        for i in range(100):
            data.add_keyword_group(
                KeywordGroup.from_lists(
                    [f"existing{i}j{j}" for j in range(3)], [])
            )

        benchmark(
            data.add_keyword_group,
            KeywordGroup.from_lists([f"newkeyword{i}" for i in range(5)], []),
        )

    def perf_add_many_keywords(self, benchmark, storage_data_class):
        """Benchmark adding many keyword groups sequentially."""
        data = storage_data_class()
        keywords_to_add = [
            [f"group{i}j{j}" for j in range(5)] for i in range(100)]

        def add_all():
            for group in keywords_to_add:
                data.add_keyword_group(KeywordGroup.from_lists(group, []))

        benchmark(add_all)
