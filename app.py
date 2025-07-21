import streamlit as st
import pandas as pd
import cloudscraper
import requests
from datetime import datetime, timedelta
import plotly.graph_objects as go

# Alpha Vantage API Key
ALPHA_VANTAGE_KEY = st.secrets.alpha_vantage.key

# Streamlit setup
st.set_page_config(page_title="Insider Trading Dashboard", layout="wide")
st.title("📈 Insider Trading Dashboard")

# Intro blurb explaining platform
st.markdown(
    """
    This dashboard aggregates insider trading data from OpenInsider and overlays it with price charts, helping you spot significant buy/sell clusters and signals.
    Use the controls on the left to customize feeds, cluster criteria, and price data views.
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

def normalize_cols(cols): return [str(c).replace("\xa0"," ").strip() for c in cols]

def find_table_with_filing(tables):
    for tbl in tables:
        cols = normalize_cols(tbl.columns)
        if "Filing Date" in cols and "Trade Date" in cols:
            tbl.columns = cols
            return tbl
    return None

def find_col(cols,*keywords):
    for c in cols:
        low = c.lower()
        for kw in keywords:
            if kw in low:
                return c
    return None

# Signal strength calculation

def calculate_signal_strength(row):
    score = 0
    if row['Shares'] >= 1_000_000: score += 35
    elif row['Shares'] >= 500_000: score += 25
    elif row['Shares'] >= 100_000: score += 15
    elif row['Shares'] >= 25_000: score += 5
    title = row['Title'].lower()
    if 'ceo' in title: score += 30
    elif 'cfo' in title: score += 20
    elif 'director' in title or 'officer' in title: score += 10
    if row['Price'] <= 2: score += 10
    elif row['Price'] <= 5: score += 5
    return score

# Cluster detection
def detect_clusters(df, days_window=7, min_insiders=3):
    clusters = []
    for ticker, grp in df.groupby('Ticker'):
        grp = grp.sort_values('TradeDate')
        for _, r in grp.iterrows():
            start = r['TradeDate'] - timedelta(days=days_window)
            window = grp[(grp['TradeDate'] >= start) & (grp['TradeDate'] <= r['TradeDate'])]
            ins = window['InsiderName'].nunique()
            if ins >= min_insiders:
                clusters.append({
                    'Ticker': ticker,
                    'WindowStart': start,
                    'EndDate': r['TradeDate'],
                    'NumInsiders': ins,
                    'TotalShares': window['Shares'].sum(),
                    'ClusterScore': window['SignalStrength'].sum() + ins * 5
                })
    return pd.DataFrame(clusters)

# Fetch daily adjusted close data
def fetch_price_data(symbol):
    params = {
        'function': 'TIME_SERIES_DAILY_ADJUSTED',
        'symbol': symbol,
        'outputsize': 'compact',
        'apikey': ALPHA_VANTAGE_KEY
    }
    r = requests.get('https://www.alphavantage.co/query', params=params).json()
    data = r.get('Time Series (Daily)', {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if '5. adjusted close' not in df.columns:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df = df.rename(columns={'5. adjusted close': 'AdjClose'})
    return df[['AdjClose']].sort_index()

# Fetch intraday OHLCV data
from plotly.graph_objects import Candlestick

def fetch_intraday_data(symbol, interval='5min', outputsize='compact', adjusted=True, extended_hours=True):
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': symbol,
        'interval': interval,
        'outputsize': outputsize,
        'apikey': ALPHA_VANTAGE_KEY,
        'adjusted': str(adjusted).lower(),
        'extended_hours': str(extended_hours).lower()
    }
    r = requests.get('https://www.alphavantage.co/query', params=params).json()
    key = f'Time Series ({interval})'
    data = r.get(key, {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df.columns = [c.split('. ')[1] for c in df.columns]
    df = df.rename(columns={'close': 'AdjClose'})
    return df.sort_index()

# Sidebar controls
feeds = st.sidebar.multiselect(
    "Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"]
)
min_insiders = st.sidebar.number_input(
    "Min insiders for cluster", min_value=2, max_value=10, value=3
)
days_window = st.sidebar.number_input(
    "Cluster window days", min_value=1, max_value=30, value=7
)
use_intraday = st.sidebar.checkbox("Use Intraday Data", value=False)
interval = st.sidebar.selectbox(
    "Intraday interval",
    ["1min", "5min", "15min", "30min", "60min"],
    index=1
)
refresh = st.sidebar.button("🔄 Refresh Data")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    ### How to Use This Dashboard
    - Select feeds and cluster settings.
    - Toggle Intraday and choose interval for OHLCV.
    - Click Refresh to fetch insider trades and price data.
    """
)

# Data fetch and load
if refresh:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    all_dfs = []
    for name in feeds:
        ep = FEEDS[name]
        dfs = []
        for off in [0, 100, 200]:
            sep = '&' if '?' in ep else '?'
            url = f"http://openinsider.com/{ep}{sep}o={off}"
            tables = pd.read_html(scraper.get(url).text, flavor='bs4')
            df0 = find_table_with_filing(tables)
            if df0 is None:
                continue
            cols = df0.columns.tolist()
            mapping = {
                'FilingDate': find_col(cols, 'filing date'),
                'TradeDate': find_col(cols, 'trade date'),
                'Ticker': find_col(cols, 'ticker'),
                'InsiderName': find_col(cols, 'insider name'),
                'Title': find_col(cols, 'title'),
                'TradeType': find_col(cols, 'trade type'),
                'Shares': find_col(cols, 'qty', 'share'),
                'Price': find_col(cols, 'price'),
            }
            if any(v is None for v in mapping.values()):
                continue
            d = pd.DataFrame({
                'FilingDate': df0[mapping['FilingDate']],
                'TradeDate': pd.to_datetime(df0[mapping['TradeDate']]),
                'Ticker': df0[mapping['Ticker']],
                'InsiderName': df0[mapping['InsiderName']],
                'Title': df0[mapping['Title']],
                'TradeType': df0[mapping['TradeType']],
                'Shares': df0[mapping['Shares']]
                    .astype(str)
                    .replace(r"[+,]", "", regex=True)
                    .astype(int),
                'Price': df0[mapping['Price']]
                    .astype(str)
                    .replace(r"[\$,]", "", regex=True)
                    .astype(float),
            })
            d = d[d['TradeType'].str.contains('purchase', case=False, na=False)]
            d['SignalStrength'] = d.apply(calculate_signal_strength, axis=1)
            dfs.append(d)
        if dfs:
            all_dfs.append(pd.concat(dfs, ignore_index=True))
    data = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    st.session_state['data'] = data
    st.success(f"✅ Fetched {len(data)} insider buys.")
else:
    data = st.session_state.get('data', pd.DataFrame())

if data.empty:
    st.info("No data to display. Please refresh.")
    st.stop()

# Display tables
col1, col2 = st.columns((2,1))
col1.markdown("### All Insider Buys")
col1.dataframe(data[['FilingDate', 'TradeDate', 'Ticker', 'InsiderName', 'Title', 'Shares', 'Price', 'SignalStrength']], use_container_width=True)
col2.markdown("### Top 5 by Signal Strength")
col2.dataframe(data.nlargest(5, 'SignalStrength')[['Ticker', 'InsiderName', 'Shares', 'Price', 'SignalStrength']], use_container_width=True)

# Cluster analysis & price chart
clusters = detect_clusters(data, days_window=days_window, min_insiders=min_insiders)
if not clusters.empty:
    st.markdown("---")
    st.markdown("## Clustered Insider Trading Analysis")
    st.dataframe(clusters.sort_values('ClusterScore', ascending=False), use_container_width=True)
    ticker_choice = st.selectbox("Select ticker for price chart", options=clusters['Ticker'].unique())
        # Fetch price data
    if use_intraday:
        price_df = fetch_intraday_data(ticker_choice, interval)
        chart_type = 'intraday'
    else:
        price_df = fetch_price_data(ticker_choice)
        chart_type = 'daily'

    # Handle missing price data
    if price_df.empty or 'AdjClose' not in price_df.columns:
        st.warning(f"No price data available for {ticker_choice}.")
    else:
        fig = go.Figure()
        if chart_type == 'intraday':
            fig.add_trace(Candlestick(
                x=price_df.index,
                open=price_df['open'], high=price_df['high'],
                low=price_df['low'], close=price_df['AdjClose'],
                name='Intraday'
            ))
        else:
            fig.add_trace(go.Scatter(
                x=price_df.index, y=price_df['AdjClose'], mode='lines', name='Adj Close'
            ))
        # Overlay clusters
        for _, cl in clusters[clusters['Ticker'] == ticker_choice].iterrows():
            val = price_df['AdjClose'].get(cl['EndDate'])
            fig.add_trace(go.Scatter(
                x=[cl['EndDate']], y=[val], mode='markers', marker=dict(size=8 + cl['NumInsiders'] * 2), name='Cluster'
            ))
        st.plotly_chart(fig, use_container_width=True)(fig, use_container_width=True)
