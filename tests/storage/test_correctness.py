"""Correctness tests for storage.py - keyword matching, group management, edge cases."""

from storage import KeywordGroup, ResolvedChat

# ============================================================================
# CORRECTNESS TESTS - KEYWORD MATCHING
# ============================================================================


class TestKeywordMatching:
    """Tests for matches_keywords method - correctness."""

    def test_group_match_and_multi_keyword(self, storage_data_class):
        """Test group matching (all keywords in one group) and multi-keyword matching.

        Start with a multi-keyword group, then add single-keyword groups and test
        that both the group match and independent matches work on the same data.
        """
        data = storage_data_class()

        # Part 1: Group match - all keywords must be present in same group
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        matched, found = data.matches_keywords("hello world")
        assert matched
        assert "hello" in found
        assert "world" in found

        # Part 2: Add single-keyword groups and verify they match independently
        data.add_keyword_group(KeywordGroup.from_lists(["the"], []))
        data.add_keyword_group(KeywordGroup.from_lists(["is"], []))
        matched, found = data.matches_keywords("the quick brown fox is here")
        assert matched
        assert "the" in found
        assert "is" in found

    def test_no_match_scenarios(self, storage_data_class):
        """Test various no-match scenarios on the same data instance."""
        # No keywords configured
        data = storage_data_class()
        matched, found = data.matches_keywords("any text here")
        assert not matched
        assert found == set()

        # Add keywords but text doesn't match
        data.add_keyword_group(KeywordGroup.from_lists(
            ["python", "javascript"], []))
        matched, found = data.matches_keywords(
            "the quick brown fox jumps over the lazy dog"
        )
        assert not matched
        assert found == set()

        # Empty text
        matched, found = data.matches_keywords("")
        assert not matched
        assert found == set()

    def test_word_boundary_rejections(self, storage_data_class):
        """Test that matches from the middle of words are rejected.

        Uses a single keyword group and tests different texts where the keyword
        appears at invalid positions (preceded by alpha characters).
        """
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["world"], []))

        # 'world' at start of 'helloworld' - no word boundary before
        matched, found = data.matches_keywords("helloworld")
        assert not matched
        assert "world" not in found

        # 'world' in middle of 'starthelloworldend' - preceded by 'd' (alpha)
        matched, found = data.matches_keywords("starthelloworldend now")
        assert not matched
        assert "world" not in found

    def test_word_boundary_accepts_various_delimiters(self, storage_data_class):
        """Test that keywords match after spaces, punctuation, numbers, underscores, unicode."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))

        # After space
        matched, found = data.matches_keywords("this is a test message")
        assert matched
        assert "test" in found

        # After punctuation
        matched, found = data.matches_keywords("hello!test here")
        assert matched
        assert "test" in found

        # After number
        matched, found = data.matches_keywords("item 42test now")
        assert matched
        assert "test" in found

        # After special characters
        matched, found = data.matches_keywords("<test>")
        assert matched
        assert "test" in found

        matched, found = data.matches_keywords("[test]")
        assert matched
        assert "test" in found

        matched, found = data.matches_keywords("(test)")
        assert matched
        assert "test" in found

        # Underscore is not alpha, so it acts as a boundary
        matched, found = data.matches_keywords("hello_world_test_here")
        assert matched
        assert "test" in found

        # Unicode characters don't block ASCII keyword matching
        matched, found = data.matches_keywords("hello 世界 test 地球")
        assert matched
        assert "test" in found

    def test_case_insensitive_matching(self, storage_data_class):
        """Test that matching is case-insensitive."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["HELLO"], []))

        matched, found = data.matches_keywords("hello world")
        assert matched
        assert "hello" in found  # keyword stored lowercase

        matched, found = data.matches_keywords("HeLLo WoRLd")
        assert matched
        assert "hello" in found

    def test_group_match_requires_all_keywords(self, storage_data_class):
        """Test that a group match requires ALL keywords in the group."""
        data = storage_data_class()
        data.add_keyword_group(
            KeywordGroup.from_lists(["critical", "system"], []))

        # Only 'critical' found, not 'system' - should NOT match the group
        matched, found = data.matches_keywords("this is critical")
        assert not matched

        # Both found - should match
        matched, found = data.matches_keywords(
            "this is a critical system error")
        assert matched
        assert "critical" in found
        assert "system" in found

    def test_overlapping_keywords(self, storage_data_class):
        """Test handling of overlapping keywords like 'python'/'pythonic' and 'act'/'action'."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["python"], []))
        data.add_keyword_group(KeywordGroup.from_lists(["pythonic"], []))

        # Match 'python' in 'python programming'
        matched, found = data.matches_keywords("python programming")
        assert matched
        assert "python" in found

        # Match 'pythonic' - 'python' also matches inside it
        matched, found = data.matches_keywords("this is pythonic code")
        assert matched
        assert "pythonic" in found
        assert "python" in found

        # Overlapping substring keywords
        data.add_keyword_group(KeywordGroup.from_lists(["act", "action"], []))

        # 'action' alone - both 'act' and 'action' should match
        matched, found = data.matches_keywords("take action now")
        assert matched
        assert "action" in found
        assert "act" in found

        # Both as separate words
        matched, found = data.matches_keywords("act or action")
        assert matched
        assert "act" in found
        assert "action" in found


# ============================================================================
# CORRECTNESS TESTS - KEYWORD GROUP MANAGEMENT
# ============================================================================


class TestKeywordGroupManagement:
    """Tests for keyword group CRUD operations."""

    def test_add_and_list_groups(self, storage_data_class):
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        assert data.keyword_group_count() == 1

        data.add_keyword_group(KeywordGroup.from_lists(["foo", "bar"], []))
        assert data.keyword_group_count() == 2

        groups = list(data.list_keyword_groups())
        assert len(groups) == 2
        assert "hello_world" in groups
        assert "bar_foo" in groups

    def test_duplicate_group_not_added_twice(self, storage_data_class):
        """Adding the same group (different order) should not duplicate."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["hello", "world"], []))
        data.add_keyword_group(
            KeywordGroup.from_lists(["world", "hello"], [])
        )  # same group, different order
        assert data.keyword_group_count() == 1

    def test_remove_groups(self, storage_data_class):
        data = storage_data_class()
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

    def test_toggle_paused(self, storage_data_class):
        data = storage_data_class()
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

    def test_add_and_list_chats(self, storage_data_class):
        data = storage_data_class()
        data.add_chat(ResolvedChat(username="123456", id=123456, topic_id=789))
        assert data.chat_count() == 1

        # Multiple topics for the same chat are merged into one MonitoredChat
        data.add_chat(ResolvedChat(username="123456", id=123456, topic_id=1))
        data.add_chat(ResolvedChat(username="123456", id=123456, topic_id=2))
        assert data.chat_count() == 1
        monitored = data._monitored_chats[123456]
        assert monitored.id == 123456
        assert monitored.topic_ids == {789, 1, 2}

        # List chats with different chat IDs
        data.add_chat(ResolvedChat(username="111111", id=111111, topic_id=1))
        data.add_chat(ResolvedChat(
            username="222222", id=222222, topic_id=None))
        assert data.chat_count() == 3
        chats = list(data.list_chats())
        assert len(chats) == 3

    def test_remove_chats(self, storage_data_class):
        data = storage_data_class()
        data.add_chat(ResolvedChat(username="123456", id=123456, topic_id=789))

        result = data.remove_chat(
            ResolvedChat(username="123456", id=123456, topic_id=789)
        )
        assert result
        assert data.chat_count() == 0

        # Remove nonexistent
        result = data.remove_chat(
            ResolvedChat(username="123456", id=123456, topic_id=789)
        )
        assert not result

    def test_chat_monitoring(self, storage_data_class):
        """Test is_resolved_chat_monitored for topics, any-topic, and different chats."""
        data = storage_data_class()
        data.add_chat(ResolvedChat(username="123456", id=123456, topic_id=1))
        data.add_chat(ResolvedChat(username="123456", id=123456, topic_id=2))

        assert data.is_resolved_chat_monitored(
            ResolvedChat(username="123456", id=123456, topic_id=1)
        )
        assert data.is_resolved_chat_monitored(
            ResolvedChat(username="123456", id=123456, topic_id=2)
        )
        assert not data.is_resolved_chat_monitored(
            ResolvedChat(username="123456", id=123456, topic_id=999)
        )

        # None in topic_ids matches any thread
        data.add_chat(ResolvedChat(
            username="999999", id=999999, topic_id=None))
        assert data.is_resolved_chat_monitored(
            ResolvedChat(username="999999", id=999999, topic_id=1)
        )
        assert data.is_resolved_chat_monitored(
            ResolvedChat(username="999999", id=999999, topic_id=999)
        )

        # Different chat IDs don't match
        assert not data.is_resolved_chat_monitored(
            ResolvedChat(username="111111", id=111111, topic_id=1)
        )


# ============================================================================
# CORRECTNESS TESTS - AUTOMATON REBUILD
# ============================================================================


class TestAutomatonRebuild:
    """Tests for automaton rebuild on keyword changes."""

    def test_automaton_lifecycle(self, storage_data_class):
        data = storage_data_class()
        assert data._automaton is None

        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))
        assert data._automaton is not None

        data.add_keyword_group(KeywordGroup.from_lists(["hello"], []))
        assert data._automaton is not None

        data.remove_keyword_group(KeywordGroup.from_lists(["test"], []))
        assert data._automaton is not None

        data.remove_keyword_group(KeywordGroup.from_lists(["hello"], []))
        assert data._automaton is None

    def test_automaton_rebuild_after_modify_group(self, storage_data_class):
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["hello"], []))
        old_automaton = data._automaton

        data.add_keyword_group(KeywordGroup.from_lists(["world"], []))
        # Automaton should be rebuilt
        assert data._automaton is not None
        assert data._automaton is not old_automaton


# ============================================================================
# CORRECTNESS TESTS - EDGE CASES
# ============================================================================


class TestEdgeCases:
    """Edge case tests."""

    def test_very_long_text_and_keyword(self, storage_data_class):
        """Test with very long text and very long keyword."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["test"], []))

        long_text = " ".join(["word"] * 10000) + \
            " test " + " ".join(["word"] * 10000)
        matched, found = data.matches_keywords(long_text)
        assert matched
        assert "test" in found

        # Very long keyword on same data
        long_keyword = "a" * 1000
        data.add_keyword_group(KeywordGroup.from_lists([long_keyword], []))
        matched, found = data.matches_keywords(f"hello {long_keyword} world")
        assert matched
        assert long_keyword in found

    def test_multiple_groups_same_keyword(self, storage_data_class):
        """Test that adding same keyword to multiple groups works."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["test", "group1"], []))
        data.add_keyword_group(KeywordGroup.from_lists(["test", "group2"], []))

        # 'test' appears in both groups, but group match requires ALL keywords
        matched, found = data.matches_keywords("this is a test message")
        # Only 'test' found, no complete group matched
        assert not matched
        assert found == {"test"}

    def test_single_char_and_numeric_keywords(self, storage_data_class):
        """Test with single character and numeric keywords."""
        data = storage_data_class()
        data.add_keyword_group(KeywordGroup.from_lists(["x"], []))

        matched, found = data.matches_keywords("find x in this text")
        assert matched
        assert "x" in found

        # Numeric keyword on same data
        data.add_keyword_group(KeywordGroup.from_lists(["123"], []))
        matched, found = data.matches_keywords("order 123 confirmed")
        assert matched
        assert "123" in found
