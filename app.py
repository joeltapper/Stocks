# app.py

import streamlit as st
import pandas as pd
import cloudscraper
import requests
from datetime import datetime

# Streamlit setup
st.set_page_config(page_title="Insider Trading Dashboard", layout="wide")
st.title("📈 Insider Trading Dashboard")

st.markdown(
    """
    This dashboard tracks insider trading activity from public companies, pulling live data from [OpenInsider](http://openinsider.com).
    <br><br>
    You can explore high-level executive purchases and sales, filtered by trade type and amount, to identify potential market signals.
    """,
    unsafe_allow_html=True
)

# Feed definitions
FEEDS = {
    "Latest Insider Purchases":  "insider-purchases",
    "Latest Insider Sales":      "insider-sells",
    "Purchases > $25 K":         "insider-purchases?pfl=25",
    "Sales > $100 K":            "insider-sells?pfl=100",
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

# ✅ Pushcut Notification Function
def send_pushcut_notification(message_body):
    api_key = st.secrets["pushcut"]["api_key"]
    notification = st.secrets["pushcut"]["notification_name"]

    url = f"https://api.pushcut.io/v1/notifications/{notification}"
    headers = {
        "Content-Type": "application/json",
        "API-Key": api_key,
    }
    payload = {
        "text": "📈 Insider Trade Alert",
        "body": message_body,
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        st.success("✅ Pushcut notification sent!")
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Pushcut error: {e}")

# Helper functions
def normalize_cols(cols):
    return [str(c).replace("\xa0", " ").strip() for c in cols]

def find_table_with_filing(tables):
    for tbl in tables:
        cols = normalize_cols(tbl.columns)
        if "Filing Date" in cols and "Trade Date" in cols:
            tbl.columns = cols
            return tbl
    return None

def find_col(cols, *keywords):
    for c in cols:
        low = c.lower()
        for kw in keywords:
            if kw in low:
                return c
    return None

def calculate_signal_strength(row):
    score = 0
    if row["Shares"] >= 1_000_000:
        score += 35
    elif row["Shares"] >= 500_000:
        score += 25
    elif row["Shares"] >= 100_000:
        score += 15
    elif row["Shares"] >= 25_000:
        score += 5

    title = row["Title"].lower()
    if "ceo" in title or "chief executive" in title:
        score += 30
    elif "cfo" in title:
        score += 20
    elif "director" in title or "officer" in title:
        score += 10

    if row["Price"] <= 2:
        score += 10
    elif row["Price"] <= 5:
        score += 5

    return score

# Streamlit UI
feeds = st.multiselect(
    "Select OpenInsider feeds to include",
    options=list(FEEDS),
    default=["Latest Insider Purchases"],
)

# 📤 Manual Pushcut Test Section
st.markdown("---")
st.subheader("📤 Test Pushcut Notification")

if st.button("Send Test Notification"):
    test_message = (
        f"🚨 TEST ALERT ({datetime.now().strftime('%m/%d %I:%M%p')}):\n"
        f"CEO John Doe bought 1,000,000 shares of TEST at $2.00\n"
        f"Score: 95/100"
    )
    send_pushcut_notification(test_message)

# 🔄 Refresh Data Button and Logic
if st.button("🔄 Refresh Data"):
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )

    all_dfs = []
    for name in feeds:
        endpoint = FEEDS[name]
        url = f"http://openinsider.com/{endpoint}"
        resp = scraper.get(url)
        resp.raise_for_status()
        tables = pd.read_html(resp.text, flavor="bs4")
        df0 = find_table_with_filing(tables)
        if df0 is None:
            st.warning(f"Feed {name} — no table with Filing Date found")
            continue

        cols = df0.columns.tolist()
        col_map = {
            "FilingDate":  find_col(cols, "filing date"),
            "TradeDate":   find_col(cols, "trade date"),
            "Ticker":      find_col(cols, "ticker"),
            "InsiderName": find_col(cols, "insider name"),
            "Title":       find_col(cols, "title"),
            "TradeType":   find_col(cols, "trade type"),
            "Shares":      find_col(cols, "qty", "share"),
            "Price":       find_col(cols, "price"),
        }

        if any(v is None for v in col_map.values()):
            st.warning(f"Feed {name} missing columns: {col_map}")
            continue

        df = pd.DataFrame({
            "FilingDate":  df0[col_map["FilingDate"]],
            "TradeDate":   df0[col_map["TradeDate"]],
            "Ticker":      df0[col_map["Ticker"]],
            "InsiderName": df0[col_map["InsiderName"]],
            "Title":       df0[col_map["Title"]],
            "TradeType":   df0[col_map["TradeType"]],
            "Shares": df0[col_map["Shares"]].astype(str).str.replace(r"[+,]", "", regex=True).astype(int),
            "Price":  df0[col_map["Price"]].astype(str).str.replace(r"[\$,]", "", regex=True).astype(float),
        })
        df["Source"] = name
        all_dfs.append(df)

    if not all_dfs:
        st.error("🚫 No data fetched — try a different feed or check your connection.")
        st.stop()

    data = pd.concat(all_dfs, ignore_index=True)
    data = data[data["TradeType"].str.contains("purchase", case=False, na=False)]
    data["SignalStrength"] = data.apply(calculate_signal_strength, axis=1)

    top = data.loc[data["SignalStrength"].idxmax()]
    st.success(f"✅ Fetched {len(data)} insider buys.")

    st.markdown(
        f"<b>{top.InsiderName}</b> bought <b>{top.Shares:,}</b> shares of "
        f"<b>{top.Ticker}</b> at <b>${top.Price:.2f}</b> on <b>{top.FilingDate}</b> "
        f"(Signal Score: <b>{top.SignalStrength}/100</b>)",
        unsafe_allow_html=True
    )

    message = (
        f"Top Buy Signal ({datetime.now().strftime('%m/%d %I:%M%p')}):\n"
        f"{top.InsiderName} bought {top.Shares:,} shares of {top.Ticker} at ${top.Price:.2f}\n"
        f"Score: {top.SignalStrength}/100"
    )

    # ✅ Pushcut Alert if Score >= 80
    if top.SignalStrength >= 80:
        send_pushcut_notification(message)

    # Display in dashboard
    c1, c2 = st.columns((2, 1))
    with c1:
        st.markdown("### 📋 All Insider Buys")
        st.dataframe(data[[
            "FilingDate", "TradeDate", "Ticker", "InsiderName",
            "Title", "Shares", "Price", "SignalStrength", "Source"
        ]], use_container_width=True)

    with c2:
        st.markdown("### 🏆 Top 5 by Signal Strength")
        st.dataframe(data.nlargest(5, "SignalStrength")[[
            "Ticker", "InsiderName", "Shares", "Price", "SignalStrength", "Source"
        ]], use_container_width=True)
