# insider_scraper.py

import warnings  # suppress warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

import cloudscraper
import pandas as pd
from io import StringIO
import yfinance as yf
from datetime import timedelta


def fetch_insider_trades():
    # 1) Cloudflare‑aware session over HTTP
    scraper = cloudscraper.create_scraper(
        browser={"custom": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    url = "http://openinsider.com/insider-purchases"
    resp = scraper.get(url)
    resp.raise_for_status()

    # 2) Parse HTML tables from a file‑like object
    tables = pd.read_html(StringIO(resp.text))
    df = None
    for tbl in tables:
        if "Filing Date" in tbl.columns:
            df = tbl.copy()
            break
    if df is None:
        raise RuntimeError("Couldn't find the insider‑purchases table")

    # 3) Normalize and rename columns
    df.columns = df.columns.str.replace("\xa0", " ").str.strip()
    df = df.rename(columns={
        "Filing Date":  "FilingDate",
        "Insider Name": "InsiderName",
        "Trade Type":   "TradeType",
        "Qty":          "Shares",
    })

    # 4) Clean numeric columns
    df["Shares"] = (
        df["Shares"].astype(str)
                     .str.replace(r"[+,]", "", regex=True)
                     .astype(int)
    )
    df["Price"] = (
        df["Price"].astype(str)
                    .str.replace(r"[\$,]", "", regex=True)
                    .astype(float)
    )

    # 5) Keep only purchases
    df = df[df["TradeType"].str.contains("Purchase", case=False, na=False)]

    # 6) Filter for >10k shares OR exec titles
    mask = (
        (df["Shares"] > 10_000)
        | df["Title"].str.contains("CEO|Chair|Director|President", regex=True, na=False)
    )
    return df.loc[mask, ["FilingDate", "Ticker", "InsiderName", "Title", "Shares", "Price"]]


if __name__ == "__main__":
    # —— STEP 1: scrape & save insider trades
    trades = fetch_insider_trades()
    trades.to_csv("insider_trades.csv", index=False)
    print(f"→ Saved insider_trades.csv with {len(trades)} rows")

    # —— STEP 2: pull 5‑day price changes via yfinance
    trades["FilingDate"] = pd.to_datetime(trades["FilingDate"]).dt.date
    results = []

    for _, row in trades.iterrows():
        ticker = row["Ticker"]
        day0 = row["FilingDate"].strftime("%Y-%m-%d")
        day5 = (row["FilingDate"] + timedelta(days=5)).strftime("%Y-%m-%d")

        hist = yf.download(
            ticker,
            start=day0,
            end=day5,
            progress=False,
            auto_adjust=True
        )
        if hist.empty:
            continue

        p0 = hist["Close"].iloc[0]
        p5 = hist["Close"].iloc[-1]
        pct5 = round((p5 - p0) / p0 * 100, 2)
        results.append({
            "Ticker": ticker,
            "FilingDate": day0,
            "Pct_5d_Change": pct5
        })

    if results:
        pd.DataFrame(results).to_csv("price_changes.csv", index=False)
        print(f"→ Saved price_changes.csv for {len(results)} tickers")
    else:
        print("→ No price data available for any ticker")

    # —— STEP 3: insider alert recommendation
    if not trades.empty:
        top = trades.loc[trades['Shares'].idxmax()]
        print("\n🔔 Insider Alert 🔔")
        print(f"{top['InsiderName']} ({top['Title']}) bought {top['Shares']} shares of {top['Ticker']} at ${top['Price']} on {top['FilingDate']}.")
        print(f"💡 Suggestion: Consider buying {top['Ticker']} as the insider did.")
