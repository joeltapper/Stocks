# ai_prompt.py

from datetime import datetime

def build_ai_prompt(df):
    today = datetime.now().date()
    today_str = today.strftime("%B %d, %Y")
    today_trades = df[df["TradeDate"].dt.date == today]

    # If no trades today, fall back to most clustered recent day
    if not today_trades.empty:
        selected_trades = today_trades
        header_date = today_str
        fallback_note = ""
    else:
        df_sorted = df.sort_values("TradeDate", ascending=False)
        most_common_date = df_sorted["TradeDate"].dt.date.value_counts().idxmax()
        selected_trades = df[df["TradeDate"].dt.date == most_common_date]
        header_date = most_common_date.strftime("%B %d, %Y")
        fallback_note = f"\n📌 *Note: No insider trades were found for {today_str}. Falling back to the most prominent recent cluster on {header_date}.*\n"

    if selected_trades.empty:
        return f"No insider trades found recently."

    prompt = f"""You are a top-tier equity research analyst at a major hedge fund. Today’s task is to generate a deep-dive investment memo from insider trading data for {header_date}.{fallback_note}

Your job is to take each of the insider purchases below and:
- Explain what the company does in 2-3 sentences.
- Find the next earnings date. Highlight it if it is within 3 weeks.
- Determine if the insider has made similar purchases in the past and what happened to the stock after.
- Analyze the technicals: RSI, 50-day and 200-day MAs, MACD if relevant.
- Evaluate the conviction behind the trade based on role (e.g., CEO vs Director), size, and price paid.
- Suggest **entry and exit points** for a swing trade or long-term position.
- Comment on any significant price/volume patterns in the last month.
- Use precise financial language, no fluff.

Insider trade summary:"""

    for _, row in selected_trades.iterrows():
        ticker = row["Ticker"]
        insider = row["InsiderName"]
        title = row["Title"]
        shares = int(row["Shares"])
        price = float(row["Price"])
        total_value = round(shares * price, 2)
        trade_date = row["TradeDate"].strftime("%B %d, %Y")

        prompt += f"""

---
📈 **{ticker}**
- 🗓️ **Trade Date:** {trade_date}
- 🧑‍💼 **Insider:** {insider} — *{title}*
- 💵 **Shares Purchased:** {shares:,} @ ${price:.2f}
- 🧾 **Total Trade Value:** ${total_value:,.2f}

🔬 Analysis Instructions for {ticker}:
1. What does this company do? Summarize in 2–3 lines.
2. When is the next earnings call? If within 21 days, flag it.
3. Has this insider made any recent trades? If yes, how did the stock respond?
4. Review 6-month price chart. Are there any technical patterns (breakouts, consolidations, gaps)?
5. Evaluate conviction: role, trade size, and whether it was above/below current price.
6. Check RSI, 50-day, 200-day moving averages. Mention if it's overbought/oversold or showing a crossover.
7. Recommend a **good entry price** and a **target exit price** based on current momentum, valuation, and technical levels.

"""

    prompt += f"""
Please return a bullet-point summary for each stock with cited data and a bolded final investment opinion: **Buy**, **Watch**, or **Avoid**."""

    return prompt
