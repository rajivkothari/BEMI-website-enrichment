"""Bullseye-branded Streamlit front end for website/phone enrichment.

Upload a prospect list (CSV/XLSX, including Outscraper exports) -> review the
roster -> export a cleaned file and a Bullseye payload. Reuses the existing
pipeline (src.enrich / src.google_places / src.bullseye_export); this module
is only the UI glue + branding. Run with:  streamlit run app.py
"""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd
import requests
import streamlit as st

from src import bullseye_export, ui_support
from src import io as table_io
from src.cache import SQLiteCache
from src.config import Settings
from src.enrich import enrich_record
from src.google_places import GooglePlacesClient, PlacesError

# --- Brand tokens (from bullseyemedical.ai) -------------------------------
INK = "#0a0a0a"
PAPER = "#f7f6f4"
TERRACOTTA = "#c84b2f"
GREEN = "#1f7a4d"
AMBER = "#c87f24"
SLATE = "#3a4250"

# Bullseye crosshair mark, recolored white-on-dark for the header bar.
LOGO_SVG = """
<svg viewBox="0 0 30 30" width="26" height="26" fill="none" aria-label="Bullseye">
  <line x1="10" y1="3" x2="15" y2="15" stroke="#ffffff" stroke-width="1.4" stroke-linecap="round"/>
  <line x1="3" y1="24" x2="15" y2="15" stroke="#ffffff" stroke-width="1.4" stroke-linecap="round"/>
  <line x1="28" y1="18" x2="15" y2="15" stroke="#ffffff" stroke-width="1.4" stroke-linecap="round"/>
  <circle cx="15" cy="15" r="3" fill="#c84b2f"/>
</svg>
"""

st.set_page_config(page_title="Bullseye — Website Enrichment", page_icon="🎯", layout="wide")

st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600&display=swap');
      .stApp {{ background: {PAPER}; }}
      html, body, [class*="css"], .stMarkdown, .stDataFrame {{ font-family: 'DM Sans', system-ui, sans-serif; }}
      h1, h2, h3, .be-serif {{ font-family: 'Instrument Serif', Georgia, serif; font-weight: 400; letter-spacing: .3px; }}
      header[data-testid="stHeader"] {{ background: transparent; }}
      #MainMenu, footer {{ visibility: hidden; }}

      .be-bar {{ background: {INK}; color: #fff; padding: 14px 22px; border-radius: 12px;
                 display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }}
      .be-bar .title {{ font-family: 'Instrument Serif', serif; font-size: 1.5rem; }}
      .be-bar .sub {{ margin-left: auto; color: #9a9a9a; font-size: .8rem; letter-spacing: .04em; }}

      .be-label {{ text-transform: uppercase; letter-spacing: .09em; font-size: .72rem;
                   font-weight: 600; color: {TERRACOTTA}; }}
      .be-tiles {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 6px 0 18px; }}
      .be-tile {{ flex: 1; min-width: 130px; border-radius: 12px; padding: 16px 18px; color: #fff; }}
      .be-tile .num {{ font-family: 'Instrument Serif', serif; font-size: 2.1rem; line-height: 1; }}
      .be-tile .lbl {{ text-transform: uppercase; letter-spacing: .09em; font-size: .68rem;
                       opacity: .85; margin-top: 6px; }}

      .stButton>button {{ border-radius: 10px; font-weight: 600; }}
      .stButton>button[kind="primary"] {{ background: {TERRACOTTA}; border-color: {TERRACOTTA}; }}
      .stButton>button[kind="primary"]:hover {{ background: #b53f25; border-color: #b53f25; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f'<div class="be-bar">{LOGO_SVG}<span class="title">Bullseye Medical Intelligence</span>'
    f'<span class="sub">WEBSITE &amp; PHONE ENRICHMENT</span></div>',
    unsafe_allow_html=True,
)


def tile(num, label, color) -> str:
    return f'<div class="be-tile" style="background:{color}"><div class="num">{num}</div><div class="lbl">{label}</div></div>'


def render_tiles(counts: dict) -> None:
    st.markdown(
        '<div class="be-tiles">'
        + tile(counts["total"], "Total", INK)
        + tile(counts["website_found"], "Website found", GREEN)
        + tile(counts["needs_review"], "Needs review", AMBER)
        + tile(counts["no_website"], "No website", SLATE)
        + "</div>",
        unsafe_allow_html=True,
    )


def to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    fd, name = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        table_io.write_table(df, name)
        with open(name, "rb") as handle:
            return handle.read()
    finally:
        os.unlink(name)


def to_bullseye_jsonl(df: pd.DataFrame) -> str:
    return "\n".join(
        json.dumps(bullseye_export.to_bullseye_enrichment_payload(row), ensure_ascii=False)
        for _, row in df.iterrows()
    )


def run_enrichment(work: pd.DataFrame, settings: Settings, *, verify: bool,
                   fill_gaps_only: bool, use_cache: bool) -> pd.DataFrame:
    cache = SQLiteCache(".cache/enrichment_cache.sqlite") if use_cache else None
    client = GooglePlacesClient(settings.api_key, cache=cache)
    session = requests.Session() if verify else None
    records = work.to_dict("records")
    total = len(records)
    rows = []
    bar = st.progress(0.0, text="Starting…")
    try:
        for i, record in enumerate(records):
            if fill_gaps_only and ui_support.has_existing_website(record):
                rows.append(ui_support.passthrough_row(record, region=settings.region))
            else:
                rows.append(enrich_record(
                    record, client, ui_support.OUTPUT_SCHEMA,
                    region=settings.region, fetch_details=True,
                    verify_websites=verify, verify_session=session,
                ))
            bar.progress((i + 1) / total, text=f"Processed {i + 1} of {total}")
    finally:
        if session is not None:
            session.close()
        if cache is not None:
            cache.close()
    bar.empty()
    return pd.DataFrame(rows, columns=ui_support.OUTPUT_SCHEMA)


# --- Sidebar options ------------------------------------------------------
settings = Settings.from_env()
with st.sidebar:
    st.markdown('<span class="be-label">Run options</span>', unsafe_allow_html=True)
    fill_gaps_only = st.toggle("Only look up rows missing a website", value=True,
                               help="Keep websites already in the file (e.g. Outscraper) and only call Google for the gaps.")
    verify = st.toggle("Verify homepages", value=False,
                       help="Fetch each looked-up site and confirm phone/city/state. Slower.")
    use_cache = st.toggle("Use cache", value=True, help="Reuse prior Google responses (free re-runs).")
    region = st.text_input("Phone region", value=settings.region or "US")
    settings.region = region or "US"
    st.divider()
    if settings.api_key:
        st.success("GOOGLE_MAPS_API_KEY detected", icon="✅")
    else:
        st.error("No GOOGLE_MAPS_API_KEY — set it in .env to look up websites.", icon="⚠️")

# --- Step 1: upload -------------------------------------------------------
st.markdown('<span class="be-label">Step 1 — Upload a prospect list</span>', unsafe_allow_html=True)
upload = st.file_uploader("CSV or XLSX (Outscraper exports work as-is)", type=["csv", "xlsx", "xls"])

if upload is None:
    st.info("Upload a list to begin. Columns are auto-detected; you can remap them below.")
    st.stop()

suffix = os.path.splitext(upload.name)[1].lower()
raw = (pd.read_csv(upload, dtype=str) if suffix == ".csv" else pd.read_excel(upload, dtype=str)).fillna("")
raw.columns = [str(c).strip() for c in raw.columns]

if st.session_state.get("file_name") != upload.name:
    st.session_state.clear()
    st.session_state.file_name = upload.name

st.caption(f"{len(raw)} rows · {len(raw.columns)} columns"
           + ("  ·  Outscraper export detected" if ui_support.is_outscraper(raw.columns) else ""))

# --- Step 2: column mapping ----------------------------------------------
st.markdown('<span class="be-label">Step 2 — Map columns</span>', unsafe_allow_html=True)
detected = ui_support.detect_column_mapping(raw.columns)
options = ["(none)"] + list(raw.columns)
mapping: dict = {}
cols = st.columns(4)
for field, c in zip(ui_support.REQUIRED_FIELDS, cols):
    default = detected.get(field)
    idx = options.index(default) if default in options else 0
    mapping[field] = c.selectbox(field, options, index=idx)
# Carry optional source fields (used by "fill gaps only").
for field in ui_support.OPTIONAL_FIELDS:
    mapping[field] = detected.get(field)
mapping = {k: (None if v in (None, "(none)") else v) for k, v in mapping.items()}

site_col = mapping.get("website")
if site_col:
    have = (raw[site_col].fillna("").str.strip() != "").sum()
    st.caption(f"Existing website column **{site_col}**: {have}/{len(raw)} rows already have a value"
               + ("  → those are kept; only the rest hit Google." if fill_gaps_only else ""))

# --- Step 3: run ----------------------------------------------------------
st.markdown('<span class="be-label">Step 3 — Enrich</span>', unsafe_allow_html=True)
run = st.button("🎯  Enrich All", type="primary", disabled=not settings.api_key)
if run:
    work = ui_support.build_working_df(raw, mapping)
    try:
        st.session_state.enriched = run_enrichment(
            work, settings, verify=verify, fill_gaps_only=fill_gaps_only, use_cache=use_cache,
        )
    except PlacesError as exc:
        st.error(f"Could not start: {exc}")

# --- Step 4: review + export ---------------------------------------------
if "enriched" in st.session_state:
    enriched = st.session_state.enriched
    render_tiles(ui_support.tile_counts(enriched))

    st.markdown('<span class="be-label">Step 4 — Review & clean up</span>', unsafe_allow_html=True)
    only_review = st.toggle("Show only rows needing review", value=False)

    work = enriched.reset_index(drop=True)
    review_mask = work["needs_review"].map(lambda v: v is True or str(v).strip().lower() == "true")
    disp = work[review_mask] if only_review else work

    view = ui_support.build_review_table(disp)
    edited = st.data_editor(
        view, hide_index=True, use_container_width=True, num_rows="fixed",
        column_config={
            "needs_review": st.column_config.CheckboxColumn("Review?", disabled=True, width="small"),
            "practice": st.column_config.TextColumn("Practice", disabled=True, width="large"),
            "location": st.column_config.TextColumn("Location", disabled=True),
            "phone": st.column_config.TextColumn("Phone", disabled=True),
            "website": st.column_config.LinkColumn("Website", disabled=True, width="large"),
            "confidence": st.column_config.TextColumn("Confidence", disabled=True, width="small"),
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "source": st.column_config.TextColumn("Source", disabled=True, width="small"),
            "decision": st.column_config.SelectboxColumn("Decision", options=["", "approved", "rejected", "replaced"]),
            "final_website": st.column_config.TextColumn("Final website (edit)", width="large"),
            "notes": st.column_config.TextColumn("Notes"),
        },
    )

    # Fold edits back into the full enriched frame and persist.
    edited_disp = ui_support.apply_review_edits(disp, edited)
    for col in ("review_decision", "reviewer_notes", "official_website_candidate"):
        work.loc[disp.index, col] = edited_disp[col].values
    st.session_state.enriched = work

    st.markdown('<span class="be-label">Step 5 — Export</span>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.download_button("⬇  Cleaned XLSX", to_xlsx_bytes(work),
                       file_name="enriched.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
    c2.download_button("🎯  Bullseye payload (JSONL)", to_bullseye_jsonl(work),
                       file_name="bullseye_payload.jsonl", mime="application/x-ndjson",
                       type="primary", use_container_width=True)
