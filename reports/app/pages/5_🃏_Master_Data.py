"""Master Data — card_master / player_master CRUD + PSA spec mapping."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from reports.app._components.audit import write_audit
from reports.app._components.auth import guard_localhost
from reports.app._components.psa_yaml import cards_with_tbd, load_priority, set_spec_id
from reports.app._components.ui import confirm_toggle, job_badge
from sportscards.db.models import Card, CardLadderSpecMap, Player
from sportscards.db.session import session_scope

st.set_page_config(page_title="Master Data", page_icon="🃏", layout="wide")
guard_localhost()
job_badge()
st.title("🃏 Master Data")

tab_cards, tab_players, tab_psa = st.tabs(["Cards", "Players", "PSA spec mapping"])


# --- Cards tab ---------------------------------------------------------------
with tab_cards:
    st.subheader("Browse cards")
    with session_scope() as s:
        all_cards = s.query(Card).all()
        card_rows = [
            {
                "card_id": c.card_id,
                "year": c.year,
                "manufacturer": c.manufacturer,
                "set_name": c.set_name,
                "card_number": c.card_number,
                "parallel": c.parallel,
                "player_id": c.player_id,
                "is_rookie": c.is_rookie,
            }
            for c in all_cards
        ]
    if card_rows:
        df = pd.DataFrame(card_rows)
        col1, col2, col3 = st.columns(3)
        mf_filter = col1.text_input("Manufacturer contains", "")
        set_filter = col2.text_input("Set contains", "")
        year_filter = col3.number_input("Year (0 = any)", 0, 2100, 0)
        if mf_filter:
            df = df[df["manufacturer"].str.contains(mf_filter, case=False, na=False)]
        if set_filter:
            df = df[df["set_name"].str.contains(set_filter, case=False, na=False)]
        if year_filter:
            df = df[df["year"] == year_filter]
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No cards yet.")

    st.subheader("Add card")
    with st.form("add_card"):
        year = st.number_input("Year", 1900, 2100, 2024)
        manufacturer = st.text_input("Manufacturer", "Panini")
        set_name = st.text_input("Set", "Prizm")
        card_number = st.text_input("Card number", "1")
        parallel = st.text_input("Parallel", "Base")
        player_id = st.number_input("Player ID", 1, step=1)
        is_rookie = st.checkbox("Rookie")
        has_auto = st.checkbox("Auto")
        has_patch = st.checkbox("Patch")
        is_oneofone = st.checkbox("1/1")
        ok = confirm_toggle("confirm_add_card")
        submitted = st.form_submit_button("Add", disabled=not ok)
        if submitted:
            with session_scope() as s:
                c = Card(
                    year=int(year),
                    manufacturer=manufacturer,
                    set_name=set_name,
                    card_number=card_number,
                    parallel=parallel,
                    player_id=int(player_id),
                    is_rookie=is_rookie,
                    has_auto=has_auto,
                    has_patch=has_patch,
                    is_one_of_one=is_oneofone,
                )
                s.add(c)
                s.flush()
                new_id = c.card_id
            write_audit("card_master_add", {"card_id": new_id, "set": set_name})
            st.success(f"card added with id={new_id}")
            st.rerun()


# --- Players tab -------------------------------------------------------------
with tab_players:
    st.subheader("Browse players")
    with session_scope() as s:
        rows = [
            {
                "player_id": p.player_id,
                "name": p.name,
                "position": p.position,
                "draft_year": p.draft_year,
                "draft_pick": p.draft_pick,
                "br_slug": p.br_slug,
            }
            for p in s.query(Player).all()
        ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("No players yet.")

    st.subheader("Add player")
    with st.form("add_player"):
        name = st.text_input("Name")
        position = st.text_input("Position", "")
        draft_year = st.number_input("Draft year (0 = none)", 0, 2100, 0)
        draft_pick = st.number_input("Draft pick (0 = none)", 0, 100, 0)
        br_slug = st.text_input("Basketball-Reference slug", "")
        ok = confirm_toggle("confirm_add_player")
        submitted = st.form_submit_button("Add", disabled=not ok or not name)
        if submitted:
            with session_scope() as s:
                p = Player(
                    name=name,
                    position=position or None,
                    draft_year=int(draft_year) or None,
                    draft_pick=int(draft_pick) or None,
                    br_slug=br_slug or None,
                )
                s.add(p)
                s.flush()
                new_id = p.player_id
            write_audit("player_master_add", {"player_id": new_id, "name": name})
            st.success(f"player added with id={new_id}")
            st.rerun()


# --- PSA spec mapping tab ----------------------------------------------------
with tab_psa:
    st.subheader("PSA spec mapping")
    st.caption("Cards in psa_priority.yaml with psa_spec_id == 'TBD'.")
    tbd = cards_with_tbd()
    if not tbd:
        st.success("No TBD entries.")
    else:
        with session_scope() as s:
            cards_lookup = {
                c.card_id: c for c in s.query(Card).filter(Card.card_id.in_(tbd)).all()
            }
        for cid in tbd:
            c = cards_lookup.get(cid)
            label = (
                f"card_id={cid}"
                + (f" — {c.year} {c.manufacturer} {c.set_name} #{c.card_number} {c.parallel}" if c else "")
            )
            with st.expander(label):
                with st.form(f"psa_form_{cid}"):
                    cert = st.text_input("Cert # for lookup (optional)", key=f"cert_{cid}")
                    spec_id = st.text_input("PSA spec_id", key=f"spec_{cid}")
                    cl_spec_id = st.text_input("Card Ladder spec_id (optional)", key=f"cl_{cid}")
                    lookup_clicked = st.form_submit_button("Lookup by cert#")
                    save_clicked = st.form_submit_button("Save")
                    if lookup_clicked and cert:
                        try:
                            from sportscards.ingest.psa_api import PsaClient
                            client = PsaClient()
                            payload = client.get_cert(cert)
                            looked_up = payload.get("SpecID") or payload.get("SpecId") or payload.get("spec_id")
                            if looked_up:
                                st.success(f"SpecID={looked_up} — paste into the field and Save.")
                            else:
                                st.warning(f"No SpecID in response: {payload}")
                        except Exception as e:  # pragma: no cover
                            st.error(f"lookup failed: {e}")
                    if save_clicked and spec_id:
                        set_spec_id(cid, spec_id)
                        if cl_spec_id:
                            with session_scope() as s:
                                s.add(CardLadderSpecMap(cl_spec_id=cl_spec_id, card_id=cid))
                        write_audit(
                            "psa_spec_map_save",
                            {"card_id": cid, "psa_spec_id": spec_id, "cl_spec_id": cl_spec_id or None},
                        )
                        st.success(f"saved spec_id={spec_id} for card_id={cid}")
                        st.rerun()

    st.markdown("---")
    st.caption("Current priority YAML")
    st.dataframe(pd.DataFrame(load_priority()), use_container_width=True)
