#scheduler.py

import schedule
import time
from datetime import datetime
import pandas as pd
import cloudscraper
import requests
import os
import toml

# Load secrets manually (outside Streamlit)
secrets = toml.load(".streamlit/secrets.toml")

# --- Telegram Alert ---
def send_telegram_alert(message_body):
    token = secrets["telegram"]["bot_token"]
    chat_id = secrets["telegram"]["chat_id"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message_body,
        "parse_mode": "Markdown"
    }

    requests.post(url, json=payload)


# --- Insider Trade Scrape ---
FEEDS = {
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

def calculate_signal_strength(row):
    score = 0
    if row["Shares"] >= 1_000_000: score += 35
    elif row["Shares"] >= 500_000: score += 25
    elif row["Shares"] >= 100_000: score += 15
    elif row["Shares"] >= 25_000: score += 5

    title = row["Title"].lower()
    if "ceo" in title or "chief executive" in title: score += 30
    elif "cfo" in title: score += 20
    elif "director" in title or "officer" in title: score += 10

    if row["Price"] <= 2: score += 10
    elif row["Price"] <= 5: score += 5

    return score

def run_alert_check(label):
    scraper = cloudscraper.create_scraper()
    url = f"http://openinsider.com/{FEEDS['CEO/CFO Purchases > $25 K']}"
    tables = pd.read_html(scraper.get(url).text, flavor="bs4")
    
    for tbl in tables:
        if "Filing Date" in tbl.columns and "Trade Date" in tbl.columns:
            df = tbl.copy()
            break
    else:
        print("❌ No valid table found")
        return

    df["Shares"] = df["Shares"].astype(str).str.replace(r"[+,]", "", regex=True).astype(int)
    df["Price"] = df["Price"].astype(str).str.replace(r"[\$,]", "", regex=True).astype(float)
    df["SignalStrength"] = df.apply(calculate_signal_strength, axis=1)
    df = df[df["Trade Type"].str.contains("purchase", case=False, na=False)]

    top = df.sort_values("SignalStrength", ascending=False).iloc[0]

    message = (
        f"📈 *{label} Insider Trade Alert* ({datetime.now().strftime('%m/%d %I:%M%p')}):\n"
        f"{top['Insider Name']} bought {top['Shares']:,} shares of {top['Ticker']} at ${top['Price']:.2f}\n"
        f"Score: {top['SignalStrength']}/100"
    )
    
    send_telegram_alert(message)
    print(f"✅ Sent {label} alert")

# --- Schedule ---
schedule.every().monday.at("09:30").do(run_alert_check, label="Market Open")
schedule.every().tuesday.at("09:30").do(run_alert_check, label="Market Open")
schedule.every().wednesday.at("09:30").do(run_alert_check, label="Market Open")
schedule.every().thursday.at("09:30").do(run_alert_check, label="Market Open")
schedule.every().friday.at("09:30").do(run_alert_check, label="Market Open")

schedule.every().monday.at("16:00").do(run_alert_check, label="Market Close")
schedule.every().tuesday.at("16:00").do(run_alert_check, label="Market Close")
schedule.every().wednesday.at("16:00").do(run_alert_check, label="Market Close")
schedule.every().thursday.at("16:00").do(run_alert_check, label="Market Close")
schedule.every().friday.at("16:00").do(run_alert_check, label="Market Close")

# --- Main Loop ---
print("📆 Scheduler started. Waiting for next run...")
while True:
    schedule.run_pending()
    time.sleep(30)
