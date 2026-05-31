"""
AI Service Abstraction Layer.

Currently powered by rule-based mock generators.
Architecture is LLM-ready: swap _backend to an Anthropic/OpenAI client
and the router layer stays identical.

Future: inject claude-3-opus-20240229 for generate_market_review().
"""
from datetime import date
from typing import Optional
from ..models.stock import Stock
from ..models.sector import Sector


class AIReviewGenerator:
    """Generates narrative market commentary. Currently rule-based."""

    def generate_market_review(
        self,
        market_phase: str,
        profit_effect: float,
        loss_effect: float,
        strong_sectors: list[str],
        dangerous_sectors: list[str],
        emotional_temperature: float,
        review_date: Optional[date] = None,
    ) -> str:
        d = review_date or date.today()
        phase_desc = {
            "bull_frenzy": "市场情绪高度亢奋，赚钱效应全面铺开",
            "warm": "市场整体偏暖，赚钱效应局部显现",
            "neutral": "市场分歧明显，赚钱效应与亏钱效应并存",
            "caution": "市场偏弱，操作需谨慎",
            "bear_fear": "市场情绪低迷，亏钱效应主导",
        }.get(market_phase, "市场状态不明")

        strong_str = "、".join(strong_sectors) if strong_sectors else "暂无"
        danger_str = "、".join(dangerous_sectors) if dangerous_sectors else "暂无"

        return (
            f"【{d} 市场复盘】\n\n"
            f"{phase_desc}。赚钱效应指数 {profit_effect:.0f}，"
            f"亏钱效应指数 {loss_effect:.0f}，情绪温度计读数 {emotional_temperature:.0f}。\n\n"
            f"活跃板块：{strong_str}。\n"
            f"高风险板块：{danger_str}。\n\n"
            f"操作建议：根据当前市场状态，仓位管理优先，严控回撤，"
            f"聚焦强势板块龙头，回避高位分歧个股。\n\n"
            f"⚠️ 本复盘为辅助分析，不构成投资建议。"
        )

    def summarize_sector(self, sector: Sector) -> str:
        from ..schemas.sector import PHASE_LABELS_ZH
        phase_zh = PHASE_LABELS_ZH.get(sector.phase, "未知")
        return (
            f"【{sector.name}】处于 {phase_zh}，"
            f"情绪得分 {sector.emotion_score:.0f}，"
            f"风险得分 {sector.risk_score:.0f}，"
            f"当前强势股数量 {sector.strong_stock_count}，"
            f"板块连板高度 {sector.board_height}。"
        )

    def summarize_stock(self, stock: Stock) -> str:
        return (
            f"【{stock.name}（{stock.code}）】"
            f"龙头得分 {stock.leader_score:.0f}，"
            f"风险得分 {stock.risk_score:.0f}，"
            f"60日最高连板 {stock.board_count_60d}，"
            f"60日涨停天数 {stock.limit_up_days_60d}，"
            f"当前阶段：{stock.phase or '未分类'}。"
        )

    def explain_signal(
        self,
        signal_type: str,
        stock_name: str,
        confidence: float,
        risk_level: str,
    ) -> str:
        type_desc = {
            "weak_to_strong": "弱转强信号",
            "broken_board_recovery": "炸板修复信号",
            "divergence_repair": "分歧修复信号",
            "rebound_acceleration": "反弹加速信号",
            "sector_repair_sync": "板块修复同步信号",
            "emotional_recovery": "情绪修复信号",
        }.get(signal_type, "技术信号")

        risk_desc = {"low": "低", "medium": "中", "high": "高"}.get(risk_level, "中")

        return (
            f"{stock_name} 出现【{type_desc}】，置信度 {confidence:.0f}，"
            f"风险等级：{risk_desc}。建议结合板块整体走势和个股量价关系综合判断，"
            f"不构成买卖建议。"
        )


# Singleton instance — swap backend here for LLM integration
ai_generator = AIReviewGenerator()
