"""Runtime layer — schedulers, bar-close detection, websocket
broadcasters, and anything else that turns "we have models + data"
into "the system actually does things over time".

Today this hosts the APScheduler integration that fires ingestion
jobs on KR/US close + macro daily refresh. Phase 4 adds the
decision-loop runtime that consumes the ingested data.
"""
