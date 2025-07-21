import streamlit as st
import pandas as pd
import cloudscraper
import requests
from datetime import datetime, timedelta
import plotly.graph_objects as go

# Streamlit setup
st.set_page_config(page_title="Insider Trading Dashboard", layout="wide")
st.title("📈 Insider Trading Dashboard")

# Alpha Vantage API Key
ALPHA_VANTAGE_KEY = st.secrets.get("alpha_vantage_key", "DB0I9TW82MKUFXSS")

st.markdown(
    """
    This dashboard aggregates and analyzes insider trading activity from public companies, providing you clear signals on significant buy/sell events.  
    It integrates data from [OpenInsider](http://openinsider.com) and overlays it with historical price charts.  
    New features: clustered insider trading detection, interactive price charts with cluster markers, and significance scoring.
    """,
    unsafe_allow_html=True
)

# Feed definitions
FEEDS = {
    "Latest Insider Purchases":  "insider-purchases",
    "Latest Insider Sales":      "insider-sells",
    "Purchases > $25 K":         "insider-purchases?pfl=25",
    "Sales > $100 K":            "insider-sells?pfl=100",
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

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

# Basic signal strength per trade

def calculate_signal_strength(row):
    score = 0
    # size weight
    if row['Shares'] >= 1_000_000:
        score += 35
    elif row['Shares'] >= 500_000:
        score += 25
    elif row['Shares'] >= 100_000:
        score += 15
    elif row['Shares'] >= 25_000:
        score += 5
    # seniority
    title = row['Title'].lower()
    if 'ceo' in title or 'chief executive' in title:
        score += 30
    elif 'cfo' in title:
        score += 20
    elif 'director' in title or 'officer' in title:
        score += 10
    # price marker
    if row['Price'] <= 2:
        score += 10
    elif row['Price'] <= 5:
        score += 5
    return score

# Cluster detection: group buys by ticker within window

def detect_clusters(df, days_window=7, min_insiders=3):
    clusters = []
    for ticker, group in df.groupby('Ticker'):
        grp = group.sort_values('TradeDate')
        for idx, row in grp.iterrows():
            window_start = row['TradeDate'] - timedelta(days=days_window)
            window_df = grp[(grp['TradeDate'] >= window_start) & (grp['TradeDate'] <= row['TradeDate'])]
            insiders = window_df['InsiderName'].nunique()
            if insiders >= min_insiders:
                total_shares = window_df['Shares'].sum()
                cluster_score = window_df['SignalStrength'].sum() + insiders * 5
                clusters.append({
                    'Ticker': ticker,
                    'EndDate': row['TradeDate'],
                    'NumInsiders': insiders,
                    'TotalShares': total_shares,
                    'ClusterScore': cluster_score,
                    'WindowStart': window_start
                })
    return pd.DataFrame(clusters)

# Fetch price data from Alpha Vantage
# Returns empty df if API error or missing data

def fetch_price_data(symbol):
    url = 'https://www.alphavantage.co/query'
    params = {
        'function': 'TIME_SERIES_DAILY_ADJUSTED',
        'symbol': symbol,
        'outputsize': 'compact',
        'apikey': ALPHA_VANTAGE_KEY
    }
    r = requests.get(url, params=params)
    result = r.json()
    data = result.get('Time Series (Daily)', {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty or '5. adjusted close' not in df.columns:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df = df.rename(columns={'5. adjusted close': 'AdjClose'})
    return df[['AdjClose']].sort_index()

# Sidebar inputs
feeds = st.sidebar.multiselect(
    "Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"]
)
min_insiders = st.sidebar.number_input(
    "Min insiders for cluster", min_value=2, max_value=10, value=3
)
days_window = st.sidebar.number_input(
    "Cluster window days", min_value=1, max_value=30, value=7
)

# Separator
st.sidebar.markdown("---")

# How to Use This Dashboard (moved below inputs)
st.sidebar.markdown(
    """
    ### How to Use This Dashboard
    - **Select OpenInsider feeds**: Choose which insider-trading feeds to fetch (e.g., all purchases, sales, or filtered by amount).
    - **Min insiders for cluster**: Set the minimum number of unique insiders in a time window to define a cluster.
    - **Cluster window days**: Define the rolling time frame (in days) to group insider trades into clusters.
    - **Refresh Data**: Click to fetch the latest insider trades (up to ~300 entries per feed) and update the tables and charts.
    """,
    unsafe_allow_html=True
)

# Data fetch and pagination for 300 buys
feeds = st.sidebar.multiselect(
    "Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"]
)
min_insiders = st.sidebar.number_input(
    "Min insiders for cluster", min_value=2, max_value=10, value=3
)
days_window = st.sidebar.number_input(
    "Cluster window days", min_value=1, max_value=30, value=7
)

# Data fetch and pagination for 300 buys
if st.sidebar.button("🔄 Refresh Data"):
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    all_dfs = []
    for name in feeds:
        endpoint = FEEDS[name]
        feed_dfs = []
        for offset in [0, 100, 200]:
            sep = '&' if '?' in endpoint else '?'
            url = f"http://openinsider.com/{endpoint}{sep}o={offset}"
            resp = scraper.get(url)
            resp.raise_for_status()
            tables = pd.read_html(resp.text, flavor='bs4')
            df0 = find_table_with_filing(tables)
            if df0 is None:
                continue
            cols = df0.columns.tolist()
            mapping = {
                'FilingDate': find_col(cols, 'filing date'),
                'TradeDate':  find_col(cols, 'trade date'),
                'Ticker':     find_col(cols, 'ticker'),
                'InsiderName':find_col(cols, 'insider name'),
                'Title':      find_col(cols, 'title'),
                'TradeType':  find_col(cols, 'trade type'),
                'Shares':     find_col(cols, 'qty', 'share'),
                'Price':      find_col(cols, 'price'),
            }
            if any(v is None for v in mapping.values()):
                continue
            df = pd.DataFrame({
                'FilingDate': df0[mapping['FilingDate']],
                'TradeDate':  pd.to_datetime(df0[mapping['TradeDate']]),
                'Ticker':     df0[mapping['Ticker']],
                'InsiderName':df0[mapping['InsiderName']],
                'Title':      df0[mapping['Title']],
                'TradeType':  df0[mapping['TradeType']],
                'Shares':     df0[mapping['Shares']].astype(str).replace(r"[+,]","",regex=True).astype(int),
                'Price':      df0[mapping['Price']].astype(str).replace(r"[\$,]","",regex=True).astype(float)
            })
            df = df[df['TradeType'].str.contains('purchase', case=False, na=False)]
            df['Source'] = name
            df['SignalStrength'] = df.apply(calculate_signal_strength, axis=1)
            feed_dfs.append(df)
        if feed_dfs:
            all_dfs.append(pd.concat(feed_dfs, ignore_index=True))
    if not all_dfs:
        st.error("🚫 No data fetched — try a different feed or check your connection.")
        st.stop()
    data = pd.concat(all_dfs, ignore_index=True)
    st.session_state['data'] = data
    st.success(f"✅ Fetched {len(data)} insider buys (up to ~300 per feed).")
else:
    data = st.session_state.get('data', pd.DataFrame())

if data.empty:
    st.info("No data to display. Please refresh.")
    st.stop()

# Display insider buys and top signals
col1, col2 = st.columns((2,1))
with col1:
    st.markdown("### 📋 All Insider Buys")
    st.dataframe(data[['FilingDate','TradeDate','Ticker','InsiderName','Title','Shares','Price','SignalStrength','Source']], use_container_width=True)
with col2:
    st.markdown("### 🏆 Top 5 by Signal Strength")
    st.dataframe(data.nlargest(5,'SignalStrength')[['Ticker','InsiderName','Shares','Price','SignalStrength','Source']], use_container_width=True)

# Cluster detection and visualization
clusters = detect_clusters(data, days_window=days_window, min_insiders=min_insiders)
if not clusters.empty:
    st.markdown("---")
    st.markdown("## 🔍 Clustered Insider Trading Analysis")
    st.dataframe(clusters.sort_values('ClusterScore', ascending=False), use_container_width=True)
    ticker_choice = st.selectbox("Select ticker for cluster visualization", options=clusters['Ticker'].unique())
    price_df = fetch_price_data(ticker_choice)
    if price_df.empty:
        st.warning(f"No price data available for {ticker_choice}.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=price_df.index, y=price_df['AdjClose'], mode='lines', name='Adj Close'))
        for _, cl in clusters[clusters['Ticker']==ticker_choice].iterrows():
            point = cl['EndDate']
            close_val = price_df['AdjClose'].get(point)
            fig.add_trace(go.Scatter(
                x=[point],
                y=[close_val],
                mode='markers', marker=dict(size=10+cl['NumInsiders']*3, opacity=0.7),
                name=f"Cluster {cl['NumInsiders']} insiders"
            ))
        st.plotly_chart(fig, use_container_width=True)
        sel = clusters.sort_values('ClusterScore', ascending=False).iloc[0]
        st.markdown(
            f"**Top Cluster**: {sel['Ticker']} from {sel['WindowStart'].date()} to {sel['EndDate'].date()} - "  
            f"Insiders: {sel['NumInsiders']}, Shares: {sel['TotalShares']:,}, Score: {sel['ClusterScore']}"
        )

# Test Telegram notification unchanged
if st.button("Send Test Notification"):
    test_msg = (
        f"🚨 TEST ALERT ({datetime.now().strftime('%m/%d %I:%M%p')}):\n"
        f"CEO John Doe bought 1,000,000 shares of TEST at $2.00\n"
        f"Score: 95/100"
    )
    try:
        token = st.secrets['telegram']['bot_token']
        chat_id = st.secrets['telegram']['chat_id']
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id":chat_id, "text":test_msg, "parse_mode":"Markdown"})
        st.success("✅ Telegram test sent!")
    except Exception as e:
        st.error(f"❌ Telegram error: {e}")
