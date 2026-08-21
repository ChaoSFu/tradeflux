"""
Leader Gate 纯函数单测：Core Leader Score 计算 + 同 Theme 内排名/"龙头未决"判定。
"""
from app.services.w2s_leader_gate_service import (
    compute_market_percentile,
    compute_core_leader_score,
    rank_within_theme,
    ThemeCandidate,
    CORE, BACKUP, UNDETERMINED, NON_LEADER,
)


def test_market_percentile_bounds():
    assert compute_market_percentile(50, []) == 0.0
    assert compute_market_percentile(100, [10, 20, 30]) == 1.0
    assert compute_market_percentile(0, [10, 20, 30]) == 0.0
    assert compute_market_percentile(25, [10, 20, 30]) == 2 / 3


def test_core_leader_score_rank1_beats_rank3():
    score_rank1 = compute_core_leader_score(
        sector_rank_position=1, market_percentile=0.9,
        board_count_60d=4, limit_up_days_20d=6, turnover_rate=15.0,
        yesterday_pct_change=-1.0, is_sector_leader=True,
    )
    score_rank3 = compute_core_leader_score(
        sector_rank_position=3, market_percentile=0.5,
        board_count_60d=1, limit_up_days_20d=1, turnover_rate=5.0,
        yesterday_pct_change=-8.0, is_sector_leader=False,
    )
    assert score_rank1 > score_rank3
    assert 0 <= score_rank1 <= 100
    assert 0 <= score_rank3 <= 100


def test_core_leader_score_missing_yesterday_data_gives_zero_resilience():
    score = compute_core_leader_score(
        sector_rank_position=1, market_percentile=1.0,
        board_count_60d=0, limit_up_days_20d=0, turnover_rate=None,
        yesterday_pct_change=None, is_sector_leader=False,
    )
    # rank1(40) + market_pct(15) + board(0) + capital(0) + resilience(0) + bonus(0) = 55
    assert score == 55.0


def test_rank_within_theme_clear_gap_assigns_core_backup():
    candidates = [
        ThemeCandidate(code="A", core_leader_score=80.0),
        ThemeCandidate(code="B", core_leader_score=60.0),
        ThemeCandidate(code="C", core_leader_score=40.0),
    ]
    results = {r.code: r for r in rank_within_theme(candidates, gap_threshold=8.0)}
    assert results["A"].leader_type == CORE
    assert results["A"].leader_rank == 1
    assert results["B"].leader_type == BACKUP
    assert results["B"].leader_rank == 2
    assert results["C"].leader_type == NON_LEADER
    assert results["C"].leader_rank == 3


def test_rank_within_theme_small_gap_is_undetermined():
    candidates = [
        ThemeCandidate(code="A", core_leader_score=61.0),
        ThemeCandidate(code="B", core_leader_score=58.0),
    ]
    results = {r.code: r for r in rank_within_theme(candidates, gap_threshold=8.0)}
    assert results["A"].leader_type == UNDETERMINED
    assert results["B"].leader_type == UNDETERMINED


def test_rank_within_theme_single_candidate_is_core():
    results = rank_within_theme([ThemeCandidate(code="A", core_leader_score=50.0)], gap_threshold=8.0)
    assert results[0].leader_type == CORE
    assert results[0].leader_rank == 1


def test_rank_within_theme_empty_list():
    assert rank_within_theme([], gap_threshold=8.0) == []
