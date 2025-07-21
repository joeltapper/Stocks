# app.py

import streamlit as st
import pandas as pd
import cloudscraper

st.set_page_config(page_title="Insider Trading Dashboard", layout="wide")
st.title("📈 Insider Trading Dashboard")

# we’re hitting the HTML pages over HTTP only
FEEDS = {
    "Latest Insider Purchases":  "insider-purchases",
    "Latest Insider Sales":      "insider-sells",
    "Purchases > $25 K":         "insider-purchases?pfl=25",
    "Sales > $100 K":            "insider-sells?pfl=100",
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

def normalize_cols(cols):
    # force to str, replace non‑breaking spaces, strip
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

feeds = st.multiselect(
    "Select OpenInsider feeds to include",
    options=list(FEEDS),
    default=["Latest Insider Purchases"],
)

if st.button("🔄 Refresh Data"):
    scraper = cloudscraper.create_scraper(
        browser={"browser":"chrome","platform":"windows","desktop":True}
    )

    all_dfs = []
    for name in feeds:
        endpoint = FEEDS[name]
        url      = f"http://openinsider.com/{endpoint}"

        resp = scraper.get(url)
        resp.raise_for_status()

        # parse ALL tables, then pick the one with our headers
        tables = pd.read_html(resp.text, flavor="bs4")
        df0    = find_table_with_filing(tables)
        if df0 is None:
            st.warning(f"Feed {name} — no table with Filing Date found")
            continue

        cols = df0.columns.tolist()
        # dynamically locate each column
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
        missing = [k for k,v in col_map.items() if v is None]
        if missing:
            st.warning(f"Feed {name} missing columns: {missing}")
            continue

        # build a normalized DataFrame
        df = pd.DataFrame({
            "FilingDate":  df0[col_map["FilingDate"]],
            "TradeDate":   df0[col_map["TradeDate"]],
            "Ticker":      df0[col_map["Ticker"]],
            "InsiderName": df0[col_map["InsiderName"]],
            "Title":       df0[col_map["Title"]],
            "TradeType":   df0[col_map["TradeType"]],
            "Shares": (
                df0[col_map["Shares"]]
                    .astype(str)
                    .str.replace(r"[+,]", "", regex=True)
                    .astype(int)
            ),
            "Price": (
                df0[col_map["Price"]]
                    .astype(str)
                    .str.replace(r"[\$,]", "", regex=True)
                    .astype(float)
            )
        })
        df["Source"] = name
        all_dfs.append(df)

    if not all_dfs:
        st.error("🚫 No data fetched — try a different feed or check your connection.")
        st.stop()

    data = pd.concat(all_dfs, ignore_index=True)
    data = data[data["TradeType"].str.contains("purchase", case=False, na=False)]

    # show summary + tables
    top = data.loc[data["Shares"].idxmax()]
    st.success(f"✅ Fetched {len(data)} insider buys.")
    st.markdown(
        f"**{top.InsiderName}** bought **{top.Shares:,}** shares of "
        f"**{top.Ticker}** at **${top.Price:.2f}** on **{top.FilingDate}** "
        f"(feed: *{top.Source}*)."
    )

    c1, c2 = st.columns((2,1))
    with c1:
        st.dataframe(
            data[[
                "FilingDate","TradeDate","Ticker","InsiderName",
                "Title","Shares","Price","Source"
            ]],
            use_container_width=True
        )
    with c2:
        st.dataframe(
            data.nlargest(5,"Shares")[
                ["Ticker","InsiderName","Shares","Price","Source"]
            ],
            use_container_width=True
        )
