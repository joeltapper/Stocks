import streamlit as st
import pandas as pd
import cloudscraper
import requests
from io import StringIO
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
    This dashboard aggregates insider trading data from OpenInsider and overlays it with price charts,
    helping you spot significant buy/sell clusters and signals. Use the controls on the left to fetch data,
    set cluster criteria, and simulate trades using buy/sell inputs.
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

# Signal strength calculation

def calculate_signal_strength(row):
    score = 0
    if row['Shares'] >= 1_000_000:
        score += 35
    elif row['Shares'] >= 500_000:
        score += 25
    elif row['Shares'] >= 100_000:
        score += 15
    elif row['Shares'] >= 25_000:
        score += 5
    title = row['Title'].lower()
    if 'ceo' in title:
        score += 30
    elif 'cfo' in title:
        score += 20
    elif 'director' in title or 'officer' in title:
        score += 10
    if row['Price'] <= 2:
        score += 10
    elif row['Price'] <= 5:
        score += 5
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

# Fetch intraday OHLCV data with full history, extended hours & error handling

def fetch_intraday_data(symbol, interval='5min'):
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': symbol,
        'interval': interval,
        'apikey': ALPHA_VANTAGE_KEY,
        'outputsize': 'full',          # full 30 days of bars
        'adjusted': 'true',            # split/dividend adjusted
        'extended_hours': 'true',      # include pre/post-market
        'datatype': 'csv'              # CSV for easier parsing
    }
    resp = requests.get('https://www.alphavantage.co/query', params=params)
    # first try JSON to catch rate-limit or bad-key errors
    try:
        j = resp.json()
        if isinstance(j, dict):
            if 'Note' in j:
                st.error("⏱️ Alpha Vantage rate limit hit—please wait and retry.")
            elif 'Error Message' in j:
                st.error(f"❌ Alpha Vantage error: {j['Error Message']}")
            return pd.DataFrame()
    except ValueError:
        pass

    # otherwise parse the CSV payload
    df = pd.read_csv(StringIO(resp.text), parse_dates=['timestamp'], index_col='timestamp')
    return df.sort_index()

# Sidebar controls
feeds = st.sidebar.multiselect(
    "Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"]
)
min_insiders = st.sidebar.number_input("Min insiders for cluster", 2, 10, 3)
days_window = st.sidebar.number_input("Cluster window days", 1, 30, 7)
interval = '5min'
refresh = st.sidebar.button("🔄 Refresh Data")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    ### How to Use This Dashboard
    - Select feeds and cluster settings.
    - Chart shows 5-min intraday candlesticks by default.
    - Enter buy/sell prices below to simulate returns.
    - Click Refresh to fetch data.
    """,
    unsafe_allow_html=True
)

# Data fetch
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
            mapping = {k: find_col(cols, *v) for k, v in {
                'FilingDate': ['filing date'],
                'TradeDate':  ['trade date'],
                'Ticker':     ['ticker'],
                'InsiderName':['insider name'],
                'Title':      ['title'],
                'TradeType':  ['trade type'],
                'Shares':     ['qty','share'],
                'Price':      ['price']
            }.items()}
            if any(v is None for v in mapping.values()):
                continue
            d = pd.DataFrame({
                'FilingDate':  df0[mapping['FilingDate']],
                'TradeDate':   pd.to_datetime(df0[mapping['TradeDate']]),
                'Ticker':      df0[mapping['Ticker']],
                'InsiderName': df0[mapping['InsiderName']],
                'Title':       df0[mapping['Title']],
                'TradeType':   df0[mapping['TradeType']],
                'Shares':      df0[mapping['Shares']].astype(str).replace(r"[+,]","",regex=True).astype(int),
                'Price':       df0[mapping['Price']].astype(str).replace(r"[\$,]","",regex=True).astype(float),
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
col1.markdown("### 📋 All Insider Buys")
col1.dataframe(
    data[['FilingDate','TradeDate','Ticker','InsiderName','Title','Shares','Price','SignalStrength']],
    use_container_width=True
)
col2.markdown("### 🏆 Top 5 by Signal Strength")
col2.dataframe(
    data.nlargest(5,'SignalStrength')[['Ticker','InsiderName','Shares','Price','SignalStrength']],
    use_container_width=True
)

# Cluster analysis & price chart
clusters = detect_clusters(data, days_window=days_window, min_insiders=min_insiders)
if not clusters.empty:
    st.markdown("---")
    st.markdown("## 🔍 Clustered Insider Trading Analysis")
    st.dataframe(clusters.sort_values('ClusterScore', ascending=False), use_container_width=True)
    ticker_choice = st.selectbox("Select ticker for price chart", clusters['Ticker'].unique())

    # Fetch intraday data
    price_df = fetch_intraday_data(ticker_choice, interval)
    if price_df.empty:
        st.warning(f"No price data available for {ticker_choice}.")
    else:
        fig = go.Figure()
        # intraday candlesticks
        fig.add_trace(go.Candlestick(
            x=price_df.index,
            open=price_df['open'], high=price_df['high'],
            low=price_df['low'], close=price_df['close'],
            name='Intraday'
        ))
        # Overlay clusters
        for _, cl in clusters[clusters['Ticker']==ticker_choice].iterrows():
            val = price_df['close'].asof(cl['EndDate'])
            fig.add_trace(go.Scatter(
                x=[cl['EndDate']], y=[val], mode='markers',
                marker=dict(size=8+cl['NumInsiders']*2), name='Cluster'
            ))
        # Buy/sell simulation
        buy_price = st.number_input(f"Enter BUY price for {ticker_choice}", min_value=0.0, step=0.01)
        sell_price = st.number_input(f"Enter SELL price for {ticker_choice}", min_value=0.0, step=0.01)
        if buy_price > 0 and sell_price > 0:
            ret = (sell_price - buy_price) / buy_price * 100
            st.metric("Simulated Net Return", f"{ret:.2f}%")
            fig.add_hline(y=buy_price, line_dash='dash', annotation_text='BUY At', line_color='green')
            fig.add_hline(y=sell_price, line_dash='dash', annotation_text='SELL At', line_color='red')

        st.plotly_chart(fig, use_container_width=True)

# Test Telegram notification unchanged
if st.button("Send Test Notification"):
    msg = f"🚨 TEST ALERT ({datetime.now():%m/%d %I:%M%p}): Sample alert"
    try:
        token = st.secrets['telegram']['bot_token']
        chat_id = st.secrets['telegram']['chat_id']
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
        )
        st.success("✅ Telegram test sent!")
    except Exception as e:
        st.error(f"❌ Telegram error: {e}")

# === AI Prompt Generator Section ===

# === Prompt Output Section ===
st.markdown("---")
st.subheader("🧠 Copy Daily AI Research Prompt")

ai_prompt = build_ai_prompt(data)
st.code(ai_prompt, language="text")

st.download_button("📄 Download Prompt as .txt", ai_prompt, file_name="daily_prompt.txt", mime="text/plain")

if st.button("📋 Copy Prompt to Clipboard"):
    st.success("Prompt copied! If not, manually copy from above.")
    st.markdown(
        f"""
        <script>
        navigator.clipboard.writeText(`{ai_prompt}`);
        </script>
        """,
        unsafe_allow_html=True
    )
from datetime import datetime
import io

def build_ai_prompt(df):
    today_str = datetime.now().strftime("%B %d, %Y")
    today_date = datetime.now().date()
    today_trades = df[df["TradeDate"].dt.date == today_date]

    if today_trades.empty:
        return f"No insider trades found for {today_str}."

    trade_lines = []
    for _, row in today_trades.iterrows():
        trade_lines.append(
            f"- {row['TradeDate'].strftime('%b %d, %Y')}: {row['Title']} at {row['Ticker']} bought {row['Shares']:,} shares at ${row['Price']:.2f}"
        )

    trade_text = "\n".join(trade_lines)

    prompt = f"""
You're an elite financial analyst with expertise in insider trading signals, company fundamentals, and swing trading setups.

Below is a list of recent insider purchases reported on OpenInsider as of {today_str}:

{trade_text}

TASK:
For **each ticker**, do the following:
1. Provide a 1-line summary of what the company does (sector + product/service)
2. State the company’s **market cap** and whether it's large-cap, mid-cap, or small-cap
3. Look up the **next earnings report date** and flag it if it's within 3 weeks
4. Briefly research the company and why there could be insider trading in this window

INSTRUCTIONS:
- For each **Buy**, include:
  - ✅ Entry price range
  - 🎯 Short-term price target (1–6 week swing)
  - 🛑 Stop-loss recommendation
  - Clear financial reasoning

- For **Avoid or Short** calls, explain the red flags.

Format like this:
- 📌 **[Ticker]** — [Company Name]
  - What they do:
  - Market Cap:
  - Next Earnings:
  - Insider Activity Summary:
  - Recommendation: ✅ Buy / ❌ Avoid / 📉 Consider Shorting
  - Entry: $X–$Y | Target: $Z | Stop: $W
  - Why: [brief reasoning]

Make sure recommendations reflect current market conditions. Prioritize real conviction setups. Use precise financial language.
"""
