# Tradebot  
Python forex bot using the OANDA v20 REST API (practice environment only).  
Strategy: Fibonacci swing-to-swing retracement on EURUSD and GBPJPY, H1.  
  
## Rules  
- Never place live orders. OANDA_ENV must be "practice".  
- Read credentials from .env only. Never print or log the token.  
- Build order: data download -> backtester -> signals -> demo execution.  
- Keep code simple and readable; explain trade logic in comments.  
- Write a test for every strategy rule.  
- Read docs/strategy.md before any strategy work.
- Log decisions in docs/decisions.md and backtest results in docs/backtests.md.
