# TradeFlux Memory Index

- [TradeFlux Project](project_tradeflux.md) — Full-stack A-share trading platform: stack, run commands, engine locations, Python 3.13 pydantic-core pin
- [Git Workflow](feedback_git_workflow.md) — 只在用户明确要求时才 commit/push，不自动提交

## 关键运行命令

```bash
# 后端启动
cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8000

# 每日数据更新（收盘后运行，约 50-90s）
cd backend && .venv/bin/python scripts/daily_update.py

# 板块全量同步（每周一次，约 51s）
cd backend && .venv/bin/python scripts/sync_boards.py

# K线历史初始化（首次部署或新环境，运行一次）
cd backend && .venv/bin/python scripts/seed_kline_history.py
```

## 重要架构决策（2026-06-01）

| 决策 | 说明 |
|------|------|
| 强势池入池判断 | 东财智能选股 API（`fetch_strong_pool_codes`），不再本地计算 |
| K线DB重建 | `stock_daily_snapshots.close_price` 存收盘价，历史快照重建 KLineBar，只拉今日1根 |
| 板块关联同步 | 方向反转：个股→板块（F10），只处理涨跌停+强势股池（~200只），增量更新 |
| 龙头 tag 计算 | 始终基于全量强势池对比，各分组 tab 不单独计算 |
