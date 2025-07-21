# scheduler.py

import pandas as pd
import cloudscraper
import requests
from datetime import datetime
import os
from dotenv import load_dotenv
from io import StringIO

# Load environment variables
load_dotenv(dotenv_path="env")
token   = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

# Send Telegram Alert
def send_telegram_alert(message_body):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       message_body,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print("✅ Telegram alert sent!")
    except Exception as e:
        print(f"❌ Telegram error: {e}")

# Insider trade feeds
FEEDS = {
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

def calculate_signal_strength(row):
    score = 0
    if row["Shares"] >= 1_000_000:    score += 35
    elif row["Shares"] >= 500_000:    score += 25
    elif row["Shares"] >= 100_000:    score += 15
    elif row["Shares"] >= 25_000:     score += 5

    title = row["Title"].lower()
    if "ceo" in title:                score += 30
    elif "cfo" in title:               score += 20
    elif "director" in title or "officer" in title:
        score += 10

    if row["Price"] <= 2:             score += 10
    elif row["Price"] <= 5:           score += 5

    return score

def run_alert_check(label):
    scraper = cloudscraper.create_scraper()
    url     = f"http://openinsider.com/{FEEDS[label]}"
    html    = scraper.get(url).text
    tables  = pd.read_html(StringIO(html), flavor="bs4")

    for tbl in tables:
        if "Filing Date" in tbl.columns and "Trade Date" in tbl.columns:
            df = tbl.copy()
            break
    else:
        print("❌ No valid table found")
        return

    # clean and score
    df["Shares"] = (
        df["Shares"]
        .astype(str)
        .str.replace(r"[+,]", "", regex=True)
        .astype(int)
    )
    df["Price"] = (
        df["Price"]
        .astype(str)
        .str.replace(r"[\$,]", "",  regex=True)
        .astype(float)
    )
    df["SignalStrength"] = df.apply(calculate_signal_strength, axis=1)
    df = df[df["Trade Type"].str.contains("purchase", case=False, na=False)]

    top = df.sort_values("SignalStrength", ascending=False).iloc[0]

    message = (
        f"📈 *{label} Insider Trade Alert* "
        f"({datetime.now().strftime('%m/%d %I:%M%p')}):\n"
        f"{top['Insider Name']} bought {top['Shares']:,} shares of "
        f"{top['Ticker']} at ${top['Price']:.2f}\n"
        f"Score: {top['SignalStrength']}/100"
    )

    send_telegram_alert(message)
    print(f"✅ Sent {label} alert")

if __name__ == "__main__":
    # 1) Startup confirmation
    send_telegram_alert(
        "✅ *Scheduler initialized successfully!*\n"
        "You'll receive your next insider-trade alerts at market open and close."
    )

    # 2) Run your one-off check immediately
    run_alert_check(label="CEO/CFO Purchases > $25 K")
