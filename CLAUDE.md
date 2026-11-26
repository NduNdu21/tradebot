# Tradebot  
Python trading bot using the OANDA v20 REST API (practice environment only).  
Strategy: ICT liquidity sweep → market structure shift → FVG, intraday on US index CFDs (NAS100_USD, SPX500_USD). Rules in progress; see docs/decisions.md. (Fibonacci on EURUSD/GBPJPY H1 is retired.)
  
## Rules  
- Never place live orders. OANDA_ENV must be "practice".  
- Read credentials from .env only. Never print or log the token. On the Render server the same three variables (OANDA_ENV, OANDA_TOKEN, OANDA_ACCOUNT_ID) are Render secret environment variables instead (owner approved the cloud host 2026-11-26).  
- Build order: data download -> backtester -> signals -> demo execution.  
- Keep code simple and readable; explain trade logic in comments.  
- Write a test for every strategy rule.  
- Read docs/strategy.md before any strategy work.
- Log decisions in docs/decisions.md and backtest results in docs/backtests.md.
