"""정찰 2: 모든 대체데이터 테이블의 실제 population + 날짜커버리지 + 시장/티커 연결.
무엇이 진짜 쓸 수 있고 무엇이 빈 스텁인가 — 고평균수익 이벤트신호 후보 grounding.
Usage: uv run python var/_analysis/recon_altdata.py
"""
import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
from sqlalchemy import text
from core.db import session_scope

TABLES = ["news_ticker_mentions", "news_finbert_score", "article_classifications",
          "earnings_sentiment_surprise", "earnings_call_sentiment", "disclosure_text_features",
          "insider_transactions", "institutional_holdings", "options_aggregates",
          "short_volume_daily", "gdelt_gcam", "gdelt_aux", "google_trends",
          "reddit_mentions", "wiki_pageviews", "patent_filings", "macro_series",
          "module_scores", "market_read", "market_regime", "cluster_weights"]


def dcol(cols):
    for c in ["date", "trade_date", "published_ts", "as_of_date", "ts", "observed_at",
              "filing_date", "period_end", "mention_ts", "created_at", "as_of_ts"]:
        if c in cols:
            return c
    return None


with session_scope() as s:
    have = set(r[0] for r in s.execute(text(
        "select table_name from information_schema.tables where table_schema='public'")).all())
    for t in TABLES:
        if t not in have:
            print(f"[없음]     {t}")
            continue
        cols = [c[0] for c in s.execute(text(
            f"select column_name from information_schema.columns where table_name='{t}' order by ordinal_position")).all()]
        n = s.execute(text(f"select count(*) from {t}")).scalar()
        dc = dcol(cols); rng = ""
        if dc and n:
            try:
                lo, hi = s.execute(text(f"select min({dc}), max({dc}) from {t}")).all()[0]
                rng = f" {dc}[{str(lo)[:10]}~{str(hi)[:10]}]"
            except Exception:
                pass
        mk = ""
        if "market" in cols and n:
            try:
                mks = s.execute(text(f"select market,count(*) from {t} group by market")).all()
                mk = " " + "/".join(f"{m}:{c}" for m, c in mks)
            except Exception:
                pass
        flag = "  <<< 채워짐" if n > 1000 else ("  (희박)" if n > 0 else "  (빈스텁)")
        print(f"[{n:>9,}] {t}{rng}{mk}{flag}")
    # 뉴스 실제 발행일 커버리지 (published_ts) + 티커연결
    print("\n=== 뉴스 실제 커버리지 ===")
    try:
        lo, hi = s.execute(text("select min(published_ts), max(published_ts) from news_articles")).all()[0]
        print(f"  news_articles published_ts: [{str(lo)[:10]} ~ {str(hi)[:10]}]")
        yrs = s.execute(text("select extract(year from published_ts)::int y, count(*) from news_articles group by y order by y")).all()
        print(f"  연도별: {', '.join(f'{y}:{c:,}' for y,c in yrs if y)}")
        bodied = s.execute(text("select count(*) from news_articles where body_fetched is true")).scalar()
        print(f"  본문확보(body_fetched): {bodied:,}")
    except Exception as e:
        print(f"  news err: {e}")
    if "news_ticker_mentions" in have:
        cols = [c[0] for c in s.execute(text("select column_name from information_schema.columns where table_name='news_ticker_mentions'")).all()]
        print(f"  news_ticker_mentions cols: {', '.join(cols)}")
        try:
            mm = s.execute(text("select market,count(*) from news_ticker_mentions group by market")).all()
            print(f"  news_ticker_mentions market: {'/'.join(f'{m}:{c:,}' for m,c in mm)}")
        except Exception:
            pass
