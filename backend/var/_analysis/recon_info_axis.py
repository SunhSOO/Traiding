"""정찰: 정보(뉴스·공시) 축 + 이벤트/촉매 데이터의 실제 DB 상태. 무엇이 있고 무엇이 비어있나.
Usage: uv run python var/_analysis/recon_info_axis.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
from sqlalchemy import text
from core.db import session_scope

CANDS = ["news_articles", "disclosures", "financial_facts", "short_interest",
         "forward_estimates", "fundamental_estimates", "analyst_estimates",
         "daily_prices", "universe_membership", "regime_snapshots", "decision_audit",
         "paper_orders", "paper_positions", "news_sentiment", "events"]

with session_scope() as s:
    have = [r[0] for r in s.execute(text(
        "select table_name from information_schema.tables where table_schema='public' order by table_name")).all()]
    print("=== 전체 테이블 ===")
    print(", ".join(have))
    print()
    for t in CANDS:
        if t not in have:
            print(f"[없음] {t}")
            continue
        try:
            n = s.execute(text(f"select count(*) from {t}")).scalar()
            cols = [c[0] for c in s.execute(text(
                f"select column_name from information_schema.columns where table_name='{t}' order by ordinal_position")).all()]
            datecol = next((c for c in ["published_at", "disclosed_at", "as_of_ts", "date", "created_at", "ts", "filed_at", "rcept_dt"] if c in cols), None)
            rng = ""
            if datecol and n:
                lo, hi = s.execute(text(f"select min({datecol}), max({datecol}) from {t}")).all()[0]
                rng = f"  {datecol}:[{str(lo)[:10]} ~ {str(hi)[:10]}]"
            mk = ""
            if "market" in cols and n:
                mks = s.execute(text(f"select market, count(*) from {t} group by market")).all()
                mk = "  market=" + "/".join(f"{m}:{c}" for m, c in mks)
            print(f"[{n:>9,}] {t}{rng}{mk}")
            print(f"           cols: {', '.join(cols)}")
        except Exception as e:
            print(f"[에러] {t}: {e}")
    # DART 공시/뉴스가 티커에 연결돼 있나 + 본문 존재?
    print("\n=== 정보축 사용가능성 체크 ===")
    for t, tickcol, bodycol in [("news_articles", "ticker", "body"), ("disclosures", "ticker", "title")]:
        if t not in have:
            continue
        cols = [c[0] for c in s.execute(text(
            f"select column_name from information_schema.columns where table_name='{t}'")).all()]
        tc = tickcol if tickcol in cols else ("tickers" if "tickers" in cols else None)
        bc = bodycol if bodycol in cols else ("content" if "content" in cols else ("summary" if "summary" in cols else None))
        if tc:
            linked = s.execute(text(f"select count(*) from {t} where {tc} is not null")).scalar()
            print(f"  {t}: 티커연결 {linked:,}행 (컬럼 {tc}) · 본문컬럼 {bc}")
    # financial_facts 분기 데이터 (PEAD용)
    q = s.execute(text("select period_kind, count(*) from financial_facts group by period_kind")).all()
    print(f"  financial_facts period_kind: {dict((k, v) for k, v in q)}")
    concepts = s.execute(text("select concept, count(*) from financial_facts group by concept order by count(*) desc limit 40")).all()
    print(f"  financial_facts concepts(top): {', '.join(c for c, _ in concepts)}")
