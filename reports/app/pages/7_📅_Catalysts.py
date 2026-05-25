"""Catalysts — placeholder until the catalyst-events chip lands."""
from __future__ import annotations

import streamlit as st

from reports.app._components.auth import guard_localhost
from reports.app._components.ui import job_badge

st.set_page_config(page_title="Catalysts", page_icon="📅", layout="wide")
guard_localhost()
job_badge()
st.title("📅 Catalysts")
st.info("Catalysts feed will land with the catalyst-events chip — placeholder for now.")
