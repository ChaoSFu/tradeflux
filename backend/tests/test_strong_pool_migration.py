"""
强势池 prompt 收窄迁移（2026-09-03）。

新语义只有一条：**过去60个交易日内真正打开过连续涨停高度**（PeakBoard60D >= 4）。
删掉的三条 OR 召回——近60日涨停>9 / 近10日涨停>4 / 近20日涨幅前10——召回的是
"涨得多"和"涨停频繁"，跟"打开过市场高度"是两回事。

迁移的难点不在改常量，而在**分清"旧默认值"和"用户真的自定义过"**：

  · 本模块的存储约定是"没自定义时 app_config 里没有行"，所以改常量对未自定义的
    用户自动生效——这部分架构天然满足，不需要代码。
  · 但生产上这份 prompt 是自定义的（旧默认值去掉"主板"两字），照上面的规则永远
    不会更新，收窄就不生效。所以要一次显式覆盖。
  · 而显式覆盖必须只认已知旧值，不能把用户真正的自定义一起冲掉。
"""
from app.models.app_config import AppConfig
from app.services import pool_config_service as pcs
from app.services.eastmoney_fetcher import (
    STRONG_POOL_KEYWORD, LEGACY_STRONG_POOL_KEYWORDS,
)


def _cur(db):
    row = db.query(AppConfig).filter(AppConfig.key == pcs.KEY_STRONG).first()
    return row.value if row else None


class TestMigration:
    def test_没有自定义行时什么都不做(self, db):
        """架构天然满足：无行=用代码常量，改常量即自动生效。"""
        r = pcs.migrate_strong_pool_keyword(db)
        assert r["action"] == "noop"
        assert _cur(db) is None, "不该凭空写出一行来"

    def test_旧默认值被迁移并留下原值(self, db):
        old = LEGACY_STRONG_POOL_KEYWORDS[0]
        pcs.set_pool_keyword(db, pcs.KEY_STRONG, old)
        r = pcs.migrate_strong_pool_keyword(db)
        assert r["action"] == "migrated"
        assert _cur(db) == STRONG_POOL_KEYWORD
        kept = db.query(AppConfig).filter(
            AppConfig.key == pcs.KEY_STRONG_MIGRATED_FROM).first()
        assert kept and kept.value == old, "必须留痕，否则出问题没法回滚"

    def test_生产上那个去掉主板的变体也认得出(self, db):
        """线上实际值 = 旧默认值去掉"主板"两字，它是这次迁移的真正目标。"""
        pcs.set_pool_keyword(db, pcs.KEY_STRONG, LEGACY_STRONG_POOL_KEYWORDS[1])
        assert pcs.migrate_strong_pool_keyword(db)["action"] == "migrated"
        assert _cur(db) == STRONG_POOL_KEYWORD

    def test_排版差异不该被误判成自定义(self, db):
        """空格、中英文分号的差异是排版，不是语义。"""
        messy = LEGACY_STRONG_POOL_KEYWORDS[0].replace(";", "；").replace("；", "； ")
        pcs.set_pool_keyword(db, pcs.KEY_STRONG, messy)
        assert pcs.migrate_strong_pool_keyword(db)["action"] == "migrated"

    def test_真正的自定义绝不覆盖(self, db):
        mine = "非ST;近5个交易日涨停天数大于2;流通市值小于50亿"
        pcs.set_pool_keyword(db, pcs.KEY_STRONG, mine)
        r = pcs.migrate_strong_pool_keyword(db)
        assert r["action"] == "skipped" and r["old"] == mine
        assert _cur(db) == mine, "用户自己写的 prompt 一个字都不能动"

    def test_可重复执行(self, db):
        pcs.set_pool_keyword(db, pcs.KEY_STRONG, LEGACY_STRONG_POOL_KEYWORDS[0])
        pcs.migrate_strong_pool_keyword(db)
        r2 = pcs.migrate_strong_pool_keyword(db)
        assert r2["action"] == "already"
        assert _cur(db) == STRONG_POOL_KEYWORD


class TestNewKeyword:
    def test_新prompt只保留最高连板这一条(self):
        k = STRONG_POOL_KEYWORD
        assert "最高连板数大于3" in k
        for dropped in ("涨停天数大于9", "涨停天数大于4", "涨幅前10"):
            assert dropped not in k, f"{dropped} 召回的是'涨得多'，不是'打开过高度'"

    def test_不再限定主板(self):
        assert "主板" not in STRONG_POOL_KEYWORD, \
            "创业板/科创板真达到高连板同样是高辨识度龙头，制度差异交给下游区分"

    def test_仍然排除ST和退市和次新(self):
        for must in ("非ST", "非退市股", "非新股非次新"):
            assert must in STRONG_POOL_KEYWORD
