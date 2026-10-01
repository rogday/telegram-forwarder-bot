"""Correctness tests for storage.py - keyword matching, group management, edge cases."""

from telegram_forwarder_bot.config import StorageManagerDynamicConfig
from telegram_forwarder_bot.storage import ChatTopic, Deduplicator, KeywordGroup, Subscriptions

# ============================================================================
# CORRECTNESS TESTS - KEYWORD MATCHING
# ============================================================================


class TestKeywordMatching:
    """Tests for KeywordMatcher.match - correctness."""

    def test_group_match_and_multi_keyword(self):
        """Test group matching (all keywords in one group) and multi-keyword matching.

        Start with a multi-keyword group, then add single-keyword groups and test
        that both the group match and independent matches work on the same data.
        """
        data = Subscriptions()

        # Part 1: Group match - all keywords must be present in same group
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        matched, found = data.matcher.match("hello world")
        assert matched
        assert "hello" in found
        assert "world" in found

        # Part 2: Add single-keyword groups and verify they match independently
        data.add_keyword_group(KeywordGroup.from_lists(["the"], []))
        data.add_keyword_group(KeywordGroup.from_lists(["is"], []))
        matched, found = data.matcher.match("the quick brown fox is here")
        assert matched
        assert "the" in found
        assert "is" in found

    def test_no_match_scenarios(self):
        """Test various no-match scenarios on the same data instance."""
        # No keywords configured
        data = Subscriptions()
        matched, found = data.matcher.match("any text here")
        assert not matched
        assert found == set()

        # Add keywords but text doesn't match
        data.add_keyword_group(KeywordGroup.from_lists(
            ["python", "javascript"], []))
        matched, found = data.matcher.match(
            "the quick brown fox jumps over the lazy dog"
        )
        assert not matched
        assert found == set()

        # Empty text
        matched, found = data.matcher.match("")
        assert not matched
        assert found == set()

    def test_word_boundary_rejections(self):
        """Test that matches from the middle of words are rejected.

        Uses a single keyword group and tests different texts where the keyword
        appears at invalid positions (preceded by alpha characters).
        """
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["world"], []))

        # 'world' at start of 'helloworld' - no word boundary before
        matched, found = data.matcher.match("helloworld")
        assert not matched
        assert "world" not in found

        # 'world' in middle of 'starthelloworldend' - preceded by 'd' (alpha)
        matched, found = data.matcher.match("starthelloworldend now")
        assert not matched
        assert "world" not in found

    def test_word_boundary_accepts_various_delimiters(self):
        """Test that keywords match after spaces, punctuation, numbers, underscores, unicode."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))

        # After space
        matched, found = data.matcher.match("this is a test message")
        assert matched
        assert "test" in found

        # After punctuation
        matched, found = data.matcher.match("hello!test here")
        assert matched
        assert "test" in found

        # After number
        matched, found = data.matcher.match("item 42test now")
        assert matched
        assert "test" in found

        # After special characters
        matched, found = data.matcher.match("<test>")
        assert matched
        assert "test" in found

        matched, found = data.matcher.match("[test]")
        assert matched
        assert "test" in found

        matched, found = data.matcher.match("(test)")
        assert matched
        assert "test" in found

        # Underscore is not alpha, so it acts as a boundary
        matched, found = data.matcher.match("hello_world_test_here")
        assert matched
        assert "test" in found

        # Unicode characters don't block ASCII keyword matching
        matched, found = data.matcher.match("hello 世界 test 地球")
        assert matched
        assert "test" in found

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["HELLO"], []))

        matched, found = data.matcher.match("hello world")
        assert matched
        assert "hello" in found  # keyword stored lowercase

        matched, found = data.matcher.match("HeLLo WoRLd")
        assert matched
        assert "hello" in found

    def test_group_match_requires_all_keywords(self):
        """Test that a group match requires ALL keywords in the group."""
        data = Subscriptions()
        data.add_keyword_group(
            KeywordGroup.from_lists(["critical", "system"], []))

        # Only 'critical' found, not 'system' - should NOT match the group
        matched, found = data.matcher.match("this is critical")
        assert not matched

        # Both found - should match
        matched, found = data.matcher.match(
            "this is a critical system error")
        assert matched
        assert "critical" in found
        assert "system" in found

    def test_overlapping_keywords(self):
        """Test handling of overlapping keywords like 'python'/'pythonic' and 'act'/'action'."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["python"], []))
        data.add_keyword_group(KeywordGroup.from_lists(["pythonic"], []))

        # Match 'python' in 'python programming'
        matched, found = data.matcher.match("python programming")
        assert matched
        assert "python" in found

        # Match 'pythonic' - 'python' also matches inside it
        matched, found = data.matcher.match("this is pythonic code")
        assert matched
        assert "pythonic" in found
        assert "python" in found

        # Overlapping substring keywords
        data.add_keyword_group(KeywordGroup.from_lists(["act", "action"], []))

        # 'action' alone - both 'act' and 'action' should match
        matched, found = data.matcher.match("take action now")
        assert matched
        assert "action" in found
        assert "act" in found

        # Both as separate words
        matched, found = data.matcher.match("act or action")
        assert matched
        assert "act" in found
        assert "action" in found


# ============================================================================
# CORRECTNESS TESTS - KEYWORD GROUP MANAGEMENT
# ============================================================================


class TestKeywordGroupManagement:
    """Tests for keyword group CRUD operations."""

    def test_add_and_list_groups(self):
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        assert data.keyword_group_count() == 1

        data.add_keyword_group(KeywordGroup.from_lists(["foo", "bar"], []))
        assert data.keyword_group_count() == 2

        groups = [str(group) for group in data.keyword_groups()]
        assert len(groups) == 2
        assert "hello_world" in groups
        assert "bar_foo" in groups

    def test_duplicate_group_not_added_twice(self):
        """Adding the same group (different order) should not duplicate."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        data.add_keyword_group(
            KeywordGroup.from_lists(["world", "hello"], [])
        )  # same group, different order
        assert data.keyword_group_count() == 1

    def test_remove_groups(self):
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))

        result = data.remove_keyword_group(
            KeywordGroup.from_lists(["hello", "world"], [])
        )
        assert result
        assert data.keyword_group_count() == 0

        # Remove nonexistent
        result = data.remove_keyword_group(
            KeywordGroup.from_lists(["nonexistent", "group"], [])
        )
        assert not result

    def test_toggle_paused(self):
        data = Subscriptions()
        assert not data.paused()

        data.toggle_paused()
        assert data.paused()

        data.toggle_paused()
        assert not data.paused()


# ============================================================================
# CORRECTNESS TESTS - GROUP MANAGEMENT
# ============================================================================


class TestGroupManagement:
    """Tests for telegram group/chat management."""

    def test_add_and_list_chats(self):
        data = Subscriptions()
        data.add_chat(ChatTopic(username="123456", id=123456, topic_id=789))
        assert data.chat_count() == 1

        # Multiple topics for the same chat are merged into one ChatSubscription
        data.add_chat(ChatTopic(username="123456", id=123456, topic_id=1))
        data.add_chat(ChatTopic(username="123456", id=123456, topic_id=2))
        assert data.chat_count() == 1
        monitored = data._chats[123456]
        assert monitored.id == 123456
        assert monitored.topic_ids == {789, 1, 2}

        # List chats with different chat IDs
        data.add_chat(ChatTopic(username="111111", id=111111, topic_id=1))
        data.add_chat(ChatTopic(
            username="222222", id=222222, topic_id=None))
        assert data.chat_count() == 3
        chats = list(data.list_chats())
        assert len(chats) == 3

    def test_remove_chats(self):
        data = Subscriptions()
        data.add_chat(ChatTopic(username="123456", id=123456, topic_id=789))

        result = data.remove_chat(
            ChatTopic(username="123456", id=123456, topic_id=789)
        )
        assert result
        assert data.chat_count() == 0

        # Remove nonexistent
        result = data.remove_chat(
            ChatTopic(username="123456", id=123456, topic_id=789)
        )
        assert not result

    def test_chat_monitoring(self):
        """Test is_topic_monitored for topics, any-topic, and different chats."""
        data = Subscriptions()
        data.add_chat(ChatTopic(username="123456", id=123456, topic_id=1))
        data.add_chat(ChatTopic(username="123456", id=123456, topic_id=2))

        assert data.is_topic_monitored(
            ChatTopic(username="123456", id=123456, topic_id=1)
        )
        assert data.is_topic_monitored(
            ChatTopic(username="123456", id=123456, topic_id=2)
        )
        assert not data.is_topic_monitored(
            ChatTopic(username="123456", id=123456, topic_id=999)
        )

        # None in topic_ids matches any thread
        data.add_chat(ChatTopic(
            username="999999", id=999999, topic_id=None))
        assert data.is_topic_monitored(
            ChatTopic(username="999999", id=999999, topic_id=1)
        )
        assert data.is_topic_monitored(
            ChatTopic(username="999999", id=999999, topic_id=999)
        )

        # Different chat IDs don't match
        assert not data.is_topic_monitored(
            ChatTopic(username="111111", id=111111, topic_id=1)
        )


# ============================================================================
# CORRECTNESS TESTS - AUTOMATON REBUILD
# ============================================================================


class TestMatcherRebuild:
    """Tests for matcher rebuild on keyword changes."""

    def test_matcher_is_replaced_when_groups_change(self):
        data = Subscriptions()
        assert data.matcher._automaton is None

        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))
        old_matcher = data.matcher
        assert old_matcher._automaton is not None

        data.add_keyword_group(KeywordGroup.from_lists(["hello"], []))
        assert data.matcher is not old_matcher
        assert data.matcher.match("hello")[0]

        data.remove_keyword_group(KeywordGroup.from_lists(["test"], []))
        data.remove_keyword_group(KeywordGroup.from_lists(["hello"], []))
        assert data.matcher._automaton is None


# ============================================================================
# CORRECTNESS TESTS - EDGE CASES
# ============================================================================


class TestEdgeCases:
    """Edge case tests."""

    def test_very_long_text_and_keyword(self):
        """Test with very long text and very long keyword."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))

        long_text = " ".join(["word"] * 10000) + \
            " test " + " ".join(["word"] * 10000)
        matched, found = data.matcher.match(long_text)
        assert matched
        assert "test" in found

        # Very long keyword on same data
        long_keyword = "a" * 1000
        data.add_keyword_group(KeywordGroup.from_lists([long_keyword], []))
        matched, found = data.matcher.match(f"hello {long_keyword} world")
        assert matched
        assert long_keyword in found

    def test_multiple_groups_same_keyword(self):
        """Test that adding same keyword to multiple groups works."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["test", "group1"], []))
        data.add_keyword_group(KeywordGroup.from_lists(["test", "group2"], []))

        # 'test' appears in both groups, but group match requires ALL keywords
        matched, found = data.matcher.match("this is a test message")
        # Only 'test' found, no complete group matched
        assert not matched
        assert found == {"test"}

    def test_single_char_and_numeric_keywords(self):
        """Test with single character and numeric keywords."""
        data = Subscriptions()
        data.add_keyword_group(KeywordGroup.from_lists(["x"], []))

        matched, found = data.matcher.match("find x in this text")
        assert matched
        assert "x" in found

        # Numeric keyword on same data
        data.add_keyword_group(KeywordGroup.from_lists(["123"], []))
        matched, found = data.matcher.match("order 123 confirmed")
        assert matched
        assert "123" in found


# ============================================================================
# CORRECTNESS TESTS - DEDUPLICATION
# ============================================================================


class TestDeduplicator:
    def test_seen_before_only_after_first_time(self):
        deduplicator = Deduplicator(StorageManagerDynamicConfig())

        assert not deduplicator.seen_before("hello")
        assert deduplicator.seen_before("hello")
        assert not deduplicator.seen_before("other")

    def test_least_recently_seen_is_evicted(self):
        deduplicator = Deduplicator(StorageManagerDynamicConfig(dedup_cache_size=2))
        deduplicator.seen_before("a")
        deduplicator.seen_before("b")
        deduplicator.seen_before("a")  # Refreshes "a", so "b" is the oldest
        deduplicator.seen_before("c")

        assert deduplicator.seen_before("a")
        assert not deduplicator.seen_before("b")

    def test_disabled_never_reports_duplicates(self):
        deduplicator = Deduplicator(StorageManagerDynamicConfig(dedup_enabled=False))

        assert not deduplicator.seen_before("hello")
        assert not deduplicator.seen_before("hello")
