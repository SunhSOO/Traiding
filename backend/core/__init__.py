"""Cross-cutting infrastructure for woonam-auto-trading.

Modules here are market-agnostic and have no business knowledge of
fundamental/technical/information analysis. They provide the substrate
that every domain module sits on top of: settings, DB session, logging,
LLM access, look-ahead-bias guard, risk engine, paper account.
"""
