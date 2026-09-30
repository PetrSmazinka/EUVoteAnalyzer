"""
Analyser module for EU Parliament voting data.

Computes on-demand analytics from precomputed tables and raw voting data.

Implemented metrics (see thesis §Metodika měření politického chování):
  Agreement Index (AI)  – eq. (cohesion)   – party_cohesion, country_cohesion,
                                              inter_faction_cohesion
  Loyalty index         – eq. (loajalita)   – mep_loyalty
  Participation index   – eq. (participace) – mep_participation

Additional analyses:
  compare_meps               – pairwise vote agreement between two MEPs

Performance note: methods that touch hlasovani_clena (14 M rows) build the same
_tmp_votes / _tmp_member_party temp tables used by preprocess.py.  A single
heavy call per term takes a few minutes; subsequent calls in the same session
reuse the cached tables (~seconds) when the term does not change.

Topic-area filtering is NOT yet implemented — the `oblast` table has no FK
connection to hlasovani in the current schema.  Pass `keyword` (substring on
hlasovani.predmet) as a coarse substitute where available.
"""

from itertools import combinations as _combinations
from typing import Dict, List, Optional, Tuple, Any, Set, cast
import numpy as np
import pandas as pd
from scipy.stats import pearsonr  # type: ignore

from EUVoteAnalyzer.database.orm import DB
from EUVoteAnalyzer.core.utils import ProgressPrint

# Visually distinct, chart-friendly fallback hex colors (without '#') used when
# a faction has no barva stored in the database.  The same acronym always maps to
# the same index so the color is stable across re-runs.
_FACTION_FALLBACK_COLORS: List[str] = [
    "4472C4", "ED7D31", "A9D18E", "FF5252", "00BCD4",
    "7B1FA2", "388E3C", "FFC107", "E91E63", "009688",
    "795548", "607D8B", "FF6F00", "283593", "1B5E20",
]


class Analyser:
    """On-demand analytics from precomputed and raw EP voting data."""

    def __init__(self) -> None:
        """Initialise the analyser with a database connection and empty lookup caches."""
        self._db = DB()
        self._moznosti: Dict[str, int] = {}   # vote-option text → id
        self._dochazka_ids: Dict[str, int] = {}  # attendance text → id
        self._cached_votes_term: Optional[int] = None  # term for which _tmp_votes exists
        self._cached_term: Optional[int] = None         # term for which both tmp tables exist
        self._cached_national_term: Optional[int] = None  # term for which _tmp_member_national exists
        self._cached_government_term: Optional[int] = None  # term for which _tmp_mep_votes_gov exists
        self._ep_color_cache: Optional[Tuple[Dict[int, str], Dict[str, str]]] = None

    # ------------------------------------------------------------------ #
    # Lookup helpers                                                       #
    # ------------------------------------------------------------------ #

    def _load_lookups(self) -> None:
        """Populate vote-option and attendance ID caches from the database (idempotent)."""
        if not self._moznosti:
            r = self._db.query("SELECT `id`, `text` FROM `hlasovaci_moznost`")
            if isinstance(r, list):
                self._moznosti = {str(row["text"]): int(row["id"]) for row in r}
        if not self._dochazka_ids:
            r = self._db.query("SELECT `id`, `text` FROM `dochazka`")
            if isinstance(r, list):
                self._dochazka_ids = {str(row["text"]): int(row["id"]) for row in r}

    def _yes_id(self) -> int:
        """Return the database ID for the affirmative vote option (``"+"``)."""
        self._load_lookups()
        return self._moznosti.get("+", 1)

    def _no_id(self) -> int:
        """Return the database ID for the negative vote option (``"-"``)."""
        self._load_lookups()
        return self._moznosti.get("-", 2)

    def _abstain_id(self) -> int:
        """Return the database ID for the abstention vote option (``"0"``)."""
        self._load_lookups()
        return self._moznosti.get("0", 3)

    def _present_id(self) -> int:
        """Return the database ID for the ``"přítomnost"`` attendance record."""
        self._load_lookups()
        return self._dochazka_ids.get("přítomnost", 1)

    def _active_ids(self) -> str:
        """SQL literal: comma-separated yes/no/abstain IDs."""
        return f"{self._yes_id()}, {self._no_id()}, {self._abstain_id()}"

    def _party_map(self) -> Dict[int, Dict[str, Any]]:
        """Return a ``{party_id: row}`` dict for all political subjects."""
        r = self._db.query("SELECT `id`, `zkratka`, `barva` FROM `politicky_subjekt`")
        return {int(row["id"]): row for row in r} if isinstance(r, list) else {}

    @staticmethod
    def _resolve_color(barva: Optional[str], fallback_key: str) -> str:
        """Return '#RRGGBB' from stored barva or a deterministic fallback."""
        if barva:
            return f"#{barva}"
        idx = abs(hash(fallback_key)) % len(_FACTION_FALLBACK_COLORS)
        return f"#{_FACTION_FALLBACK_COLORS[idx]}"

    def _ep_color_maps(self) -> Tuple[Dict[int, str], Dict[str, str]]:
        """Return (by_id: Dict[int,str], by_zkratka: Dict[str,str]) for EP factions."""
        if self._ep_color_cache is not None:
            return self._ep_color_cache
        rows = self._db.query(
            "SELECT `id`, `zkratka`, `barva` FROM `politicky_subjekt`"
            " WHERE `typ` = 'POLITICAL_GROUP'"
        )
        by_id: Dict[int, str] = {}
        by_zkr: Dict[str, str] = {}
        if isinstance(rows, list):
            for r in rows:
                pid   = int(r["id"])
                zkr   = str(r["zkratka"] or "")
                color = self._resolve_color(r.get("barva"), zkr or str(pid))
                by_id[pid] = color
                if zkr:
                    by_zkr[zkr] = color
        self._ep_color_cache = (by_id, by_zkr)
        return self._ep_color_cache

    def _mep_map(self) -> Dict[int, Dict[str, Any]]:
        """Return a ``{mep_id: row}`` dict containing name and citizenship for every MEP."""
        r = self._db.query(
            "SELECT `id`, `jmeno`, `prijmeni`, `obcanstvi` FROM `clen`"
        )
        return {int(row["id"]): row for row in r} if isinstance(r, list) else {}

    # ------------------------------------------------------------------ #
    # Temp-table helpers (mirrors preprocess.py for performance)           #
    # ------------------------------------------------------------------ #

    def _ensure_votes_table(self, term: int) -> None:
        """Build _tmp_votes for *term* if not already cached (fast — no date-range join)."""
        if self._cached_votes_term == term:
            return
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_votes`")
        self._db.query(f"""
            CREATE TEMPORARY TABLE `_tmp_votes` AS
            SELECT h.id, h.ck_zasedani, h.proceduralni, h.hlasovani_kategorie
            FROM `hlasovani` h
            JOIN `zasedani` z ON h.ck_zasedani = z.id
            WHERE z.ck_zastupitelstvo = {int(term)}
              AND h.validni IS NOT FALSE
        """)
        self._db.query("ALTER TABLE `_tmp_votes` ADD PRIMARY KEY (`id`)")
        self._db.query("ALTER TABLE `_tmp_votes` ADD INDEX `idx_atv_sess` (`ck_zasedani`)")
        n = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_votes`")
        cnt = n[0]["n"] if isinstance(n, list) and n else 0
        ProgressPrint.log(f"  _tmp_votes: {cnt} rows", "INFO")
        self._cached_votes_term = term

    def _ensure_term_tables(self, term: int) -> None:
        """
        Build _tmp_votes, _tmp_member_party, and _tmp_mep_votes for *term*.

        _tmp_votes        – valid hlasovani IDs + session + proceduralni (fast)
        _tmp_member_party – (session, member) → party (slow date-range join on prislusi_k)
        _tmp_mep_votes    – pre-joined (MEP, vote, option, party) for non-procedural
                            active votes; reused by _faction_vote_df and mep_loyalty
                            to avoid re-scanning hlasovani_clena (14 M rows)
        """
        if self._cached_term == term:
            return

        ProgressPrint.log(f"Building term temp tables for term {term}…", "INFO")
        self._load_lookups()
        self._ensure_votes_table(term)

        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_member_party`")
        self._db.query(f"""
            CREATE TEMPORARY TABLE `_tmp_member_party` AS
            SELECT DISTINCT z.id AS ck_zasedani, pk.ck_clen, pk.ck_subjekt
            FROM `zasedani` z
            JOIN `zastupitelstvo` zt ON zt.poradi = {int(term)}
            JOIN `prislusi_k` pk
                ON  DATE(z.datum_od)
                    BETWEEN pk.datum_od AND IFNULL(pk.datum_do, '9999-12-31')
                AND (
                    pk.ck_zastupitelstvo = {int(term)}
                    OR pk.datum_od >= zt.funkcni_obdobi_od
                )
            JOIN `politicky_subjekt` ps
                ON  ps.id = pk.ck_subjekt
                AND ps.typ = 'POLITICAL_GROUP'
            WHERE z.ck_zastupitelstvo = {int(term)}
        """)
        self._db.query(
            "ALTER TABLE `_tmp_member_party` "
            "ADD INDEX `idx_amp_sess_clen` (`ck_zasedani`, `ck_clen`)"
        )
        n_mp = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_member_party`")
        cnt_mp = n_mp[0]["n"] if isinstance(n_mp, list) and n_mp else 0
        ProgressPrint.log(f"  _tmp_member_party: {cnt_mp} rows", "INFO")

        # Single scan of hlasovani_clena for the term — reused by all subsequent analyses
        active = self._active_ids()
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_mep_votes`")
        self._db.query(f"""
            CREATE TEMPORARY TABLE `_tmp_mep_votes` AS
            SELECT hc.ck_clen, tv.id AS ck_hlasovani, hc.ck_moznost, tmp.ck_subjekt,
                   tv.hlasovani_kategorie AS kat
            FROM `_tmp_votes` tv
            STRAIGHT_JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
            JOIN `_tmp_member_party` tmp
                ON  tmp.ck_zasedani = tv.ck_zasedani
                AND tmp.ck_clen     = hc.ck_clen
            WHERE hc.ck_moznost IN ({active})
              AND tv.proceduralni IS NOT TRUE
        """)
        self._db.query(
            "ALTER TABLE `_tmp_mep_votes` "
            "ADD INDEX `idx_amv_vote_subj` (`ck_hlasovani`, `ck_subjekt`, `ck_moznost`)"
        )
        self._db.query(
            "ALTER TABLE `_tmp_mep_votes` "
            "ADD INDEX `idx_amv_clen_subj` (`ck_clen`, `ck_subjekt`)"
        )
        n_mv = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_mep_votes`")
        cnt_mv = n_mv[0]["n"] if isinstance(n_mv, list) and n_mv else 0
        ProgressPrint.log(f"  _tmp_mep_votes: {cnt_mv} rows", "INFO")
        self._cached_term = term

    def _ensure_national_table(self, term: int) -> None:
        """Build _tmp_member_national: (session, member) → national party.

        Calls _ensure_term_tables so that _tmp_member_party is available for
        cross-validation in party_topic_profile (EP group and country lookups).
        """
        if self._cached_national_term == term:
            return
        self._ensure_term_tables(term)  # builds _tmp_votes + _tmp_member_party
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_member_national`")
        self._db.query(f"""
            CREATE TEMPORARY TABLE `_tmp_member_national` AS
            SELECT DISTINCT z.id AS ck_zasedani, pk.ck_clen, pk.ck_subjekt
            FROM `zasedani` z
            JOIN `zastupitelstvo` zt ON zt.poradi = {int(term)}
            JOIN `prislusi_k` pk
                ON  DATE(z.datum_od) BETWEEN pk.datum_od AND IFNULL(pk.datum_do, '9999-12-31')
                AND (
                    pk.ck_zastupitelstvo = {int(term)}
                    OR pk.datum_od >= zt.funkcni_obdobi_od
                )
            JOIN `politicky_subjekt` ps
                ON  ps.id  = pk.ck_subjekt
                AND ps.typ = 'NATIONAL_POLITICAL_GROUP'
            WHERE z.ck_zastupitelstvo = {int(term)}
        """)
        self._db.query(
            "ALTER TABLE `_tmp_member_national` "
            "ADD INDEX `idx_amnat_sess_clen` (`ck_zasedani`, `ck_clen`)"
        )
        n = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_member_national`")
        cnt = n[0]["n"] if isinstance(n, list) and n else 0
        ProgressPrint.log(f"  _tmp_member_national: {cnt} rows", "INFO")
        self._cached_national_term = term

    def _ensure_government_table(self, term: int) -> None:
        """
        Build _tmp_mep_votes_gov: _tmp_mep_votes rows tagged with each MEP's
        national party and whether that national party was in government
        domestically when the vote was cast.

        `ve_vlade.datum_od`/`datum_do` are plain year strings (e.g. "2020",
        sometimes month-qualified like "2024 (Oct)" upstream, though this
        table only ever stores bare years) rather than precise dates, since
        that's the granularity the underlying election data provides -- so
        government periods are compared against `YEAR(hlasovani.cas)`, not
        exact date arithmetic. A government's period is treated as covering
        `YEAR(cas) >= start_year AND (datum_do IS NULL OR YEAR(cas) < end_year)`:
        the incoming government is credited starting the calendar year of the
        election that produced it.

        Coverage caveat: `ve_vlade` currently only covers each country's two
        most recent elections (~2018-2026). A national party with no matching
        `ve_vlade` row for a given vote's year is treated as being in
        opposition by default -- this is a real data-coverage limitation, not
        a verified "always opposition" fact, and should stay visibly
        disclosed wherever this analysis is presented (see the web tab's
        coverage note).
        """
        if self._cached_government_term == term:
            return
        self._ensure_national_table(term)  # builds _tmp_votes, _tmp_member_party,
                                            # _tmp_mep_votes, _tmp_member_national
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_mep_votes_gov`")
        self._db.query("""
            CREATE TEMPORARY TABLE `_tmp_mep_votes_gov` AS
            SELECT mv.ck_clen,
                   mv.ck_hlasovani,
                   mv.ck_moznost,
                   mv.ck_subjekt  AS ck_ep_group,
                   tmn.ck_subjekt AS ck_national,
                   EXISTS (
                       SELECT 1 FROM `ve_vlade` ve
                       WHERE ve.ck_subjekt = tmn.ck_subjekt
                         AND YEAR(h.cas) >= CAST(LEFT(ve.datum_od, 4) AS UNSIGNED)
                         AND (
                             ve.datum_do IS NULL
                             OR YEAR(h.cas) < CAST(LEFT(ve.datum_do, 4) AS UNSIGNED)
                         )
                   ) AS in_government
            FROM `_tmp_mep_votes` mv
            JOIN `_tmp_votes` tv ON tv.id = mv.ck_hlasovani
            JOIN `hlasovani`  h  ON h.id  = mv.ck_hlasovani
            JOIN `_tmp_member_national` tmn
                ON  tmn.ck_zasedani = tv.ck_zasedani
                AND tmn.ck_clen     = mv.ck_clen
        """)
        self._db.query(
            "ALTER TABLE `_tmp_mep_votes_gov` "
            "ADD INDEX `idx_amvg_vote_group` (`ck_hlasovani`, `ck_ep_group`, `ck_moznost`)"
        )
        self._db.query(
            "ALTER TABLE `_tmp_mep_votes_gov` "
            "ADD INDEX `idx_amvg_clen` (`ck_clen`, `ck_national`, `in_government`)"
        )
        n_gov = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_mep_votes_gov`")
        cnt_gov = n_gov[0]["n"] if isinstance(n_gov, list) and n_gov else 0
        ProgressPrint.log(f"  _tmp_mep_votes_gov: {cnt_gov} rows", "INFO")
        self._cached_government_term = term

    # ------------------------------------------------------------------ #
    # Internal: per-vote faction vote-count DataFrame                      #
    # ------------------------------------------------------------------ #

    def _faction_vote_df(self, term: int, non_procedural: bool = True) -> pd.DataFrame:
        """
        Return DataFrame(ck_hlasovani, ck_subjekt, ck_moznost, vote_count)
        — one row per (vote × faction × option) — used by loyalty and
        inter_faction_cohesion.

        Uses _tmp_mep_votes (already filtered to non-procedural active votes).
        non_procedural=False falls back to a direct query (rare path).
        """
        self._ensure_term_tables(term)
        if non_procedural:
            rows = self._db.query("""
                SELECT ck_hlasovani, ck_subjekt, ck_moznost, COUNT(*) AS vote_count
                FROM `_tmp_mep_votes`
                GROUP BY ck_hlasovani, ck_subjekt, ck_moznost
            """)
        else:
            active = self._active_ids()
            rows = self._db.query(f"""
                SELECT tv.id AS ck_hlasovani, tmp.ck_subjekt, hc.ck_moznost,
                       COUNT(*) AS vote_count
                FROM `_tmp_votes` tv
                STRAIGHT_JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
                JOIN `_tmp_member_party` tmp
                    ON  tmp.ck_zasedani = tv.ck_zasedani
                    AND tmp.ck_clen     = hc.ck_clen
                WHERE hc.ck_moznost IN ({active})
                GROUP BY tv.id, tmp.ck_subjekt, hc.ck_moznost
            """)
        if not isinstance(rows, list) or not rows:
            return pd.DataFrame(
                columns=["ck_hlasovani", "ck_subjekt", "ck_moznost", "vote_count"]
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _plurality(df: pd.DataFrame) -> pd.DataFrame:
        """
        Given (ck_hlasovani, ck_subjekt, ck_moznost, vote_count), return one row
        per (ck_hlasovani, ck_subjekt) with the plurality ck_moznost.
        Ties broken by lowest option ID (yes preferred over no over abstain).
        """
        sorted_df = df.sort_values("ck_moznost")  # tie-break: lowest id first
        idx = sorted_df.groupby(["ck_hlasovani", "ck_subjekt"])["vote_count"].idxmax()
        return (
            df.loc[idx, ["ck_hlasovani", "ck_subjekt", "ck_moznost"]]
              .rename(columns={"ck_moznost": "plurality"})
              .reset_index(drop=True)
        )

    # ================================================================== #
    # Agreement Index — party / country / inter-faction                   #
    # ================================================================== #

    def party_cohesion(self, term: int) -> List[Dict[str, Any]]:
        """
        Average Agreement Index (AI) per party for *term*.

        Uses precomputed `soudrznost_subjektu_zasedani` — fast.

        Returns list of {id, zkratka, barva, ai, sessions}.
        """
        result = self._db.query(f"""
            SELECT ps.id, ps.zkratka, ps.barva,
                   ROUND(AVG(ssz.soudrznost), 4)  AS ai,
                   COUNT(ssz.ck_zasedani)          AS sessions
            FROM `soudrznost_subjektu_zasedani` ssz
            JOIN `zasedani`          z  ON z.id  = ssz.ck_zasedani
            JOIN `politicky_subjekt` ps ON ps.id = ssz.ck_subjekt
            WHERE z.ck_zastupitelstvo = {int(term)}
              AND ssz.soudrznost IS NOT NULL
            GROUP BY ps.id, ps.zkratka, ps.barva
            ORDER BY ai DESC
        """)
        if not isinstance(result, list):
            return []
        for row in result:
            row["color"] = self._resolve_color(
                row.get("barva"), str(row.get("zkratka") or row.get("id", ""))
            )
        return result

    def country_cohesion(self, term: int) -> List[Dict[str, Any]]:
        """
        Average AI per EU member state for *term*.

        For each country and vote, computes the Agreement Index (AI) as the
        fraction of MEPs from that country who voted with the country plurality.
        Averages per-vote AIs across all non-procedural votes where ≥2 MEPs
        from the country cast an active vote.

        mep_count = distinct MEPs from that country who cast any active vote.

        Returns list of {country, ai, votes, mep_count}.
        """
        self._load_lookups()
        self._ensure_votes_table(term)
        yes = self._yes_id()
        no = self._no_id()
        abstain = self._abstain_id()
        active = self._active_ids()

        # All aggregation done in SQL; returns one row per country (~27 rows)
        rows = self._db.query(f"""
            SELECT country,
                   ROUND(AVG(vote_ai), 4) AS ai,
                   COUNT(*)               AS votes
            FROM (
                SELECT c.obcanstvi AS country,
                       tv.id       AS ck_hlasovani,
                       GREATEST(
                           SUM(hc.ck_moznost = {yes}),
                           SUM(hc.ck_moznost = {no}),
                           SUM(hc.ck_moznost = {abstain})
                       ) / COUNT(*) * 100                  AS vote_ai
                FROM `_tmp_votes` tv
                STRAIGHT_JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
                JOIN `clen` c ON c.id = hc.ck_clen
                WHERE hc.ck_moznost IN ({active})
                  AND tv.proceduralni IS NOT TRUE
                  AND c.obcanstvi IS NOT NULL
                GROUP BY c.obcanstvi, tv.id
                HAVING COUNT(*) >= 2
            ) q
            GROUP BY country
            ORDER BY ai DESC
        """)

        if not isinstance(rows, list) or not rows:
            return []

        mep_counts = self._db.query(f"""
            SELECT c.obcanstvi AS country,
                   COUNT(DISTINCT hc.ck_clen) AS mep_count
            FROM `_tmp_votes` tv
            STRAIGHT_JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
            JOIN `clen` c ON c.id = hc.ck_clen
            WHERE hc.ck_moznost IN ({active})
              AND tv.proceduralni IS NOT TRUE
              AND c.obcanstvi IS NOT NULL
            GROUP BY c.obcanstvi
        """)
        mep_map = (
            {str(r["country"]): int(r["mep_count"]) for r in mep_counts}
            if isinstance(mep_counts, list) else {}
        )

        return [
            {
                "country":   r["country"],
                "ai":        float(r["ai"]),
                "votes":     int(r["votes"]),
                "mep_count": mep_map.get(str(r["country"]), 0),
            }
            for r in rows
        ]

    def inter_faction_cohesion(self, term: int) -> List[Dict[str, Any]]:
        """
        Pairwise vote-agreement between all faction pairs for *term*.

        For each pair (A, B) counts votes where both factions' plurality decision
        matches, divided by votes where both participated.

        Returns list of {subj1_id, subj1_zkratka, subj1_barva,
                          subj2_id, subj2_zkratka, subj2_barva,
                          agreement, agreed_votes, total_votes}.
        Note: builds temp tables + loads data into pandas — may take minutes.
        """
        ProgressPrint.log(f"Computing inter-faction cohesion for term {term}…", "INFO")
        self._load_lookups()
        fv = self._faction_vote_df(term)
        if fv.empty:
            return []

        plurality = self._plurality(fv)
        pivot = plurality.pivot(
            index="ck_hlasovani", columns="ck_subjekt", values="plurality"
        )

        factions = [int(c) for c in pivot.columns]
        ps_map = self._party_map()
        results: List[Dict[str, Any]] = []

        for i, a in enumerate(factions):
            for b in factions[i + 1:]:
                both = pivot[[a, b]].dropna()
                total = len(both)
                if total == 0:
                    continue
                agreed = int((both[a] == both[b]).sum())
                pa = ps_map.get(a, {})
                pb = ps_map.get(b, {})
                results.append({
                    "subj1_id":      a,
                    "subj1_zkratka": pa.get("zkratka", str(a)),
                    "subj1_barva":   pa.get("barva"),
                    "subj1_color":   self._resolve_color(pa.get("barva"), pa.get("zkratka", str(a))),
                    "subj2_id":      b,
                    "subj2_zkratka": pb.get("zkratka", str(b)),
                    "subj2_barva":   pb.get("barva"),
                    "subj2_color":   self._resolve_color(pb.get("barva"), pb.get("zkratka", str(b))),
                    "agreement":     round(agreed / total, 4),
                    "agreed_votes":  agreed,
                    "total_votes":   total,
                })

        return sorted(results, key=lambda x: x["agreement"], reverse=True)

    # ================================================================== #
    # Participation index                                                  #
    # ================================================================== #

    def mep_participation(self, term: int) -> List[Dict[str, Any]]:
        """
        Participation index per MEP: sessions_attended / total_sessions_in_term.

        Uses precomputed `byl_pritomen` — fast.

        Returns list of {ck_clen, jmeno, prijmeni, obcanstvi,
                          pritomen, celkem, participace}.
        """
        id_present = self._present_id()
        result = self._db.query(f"""
            SELECT bp.ck_clen,
                   c.jmeno, c.prijmeni, c.obcanstvi,
                   SUM(bp.ck_dochazka = {id_present}) AS pritomen,
                   COUNT(*)                            AS celkem,
                   ROUND(
                       SUM(bp.ck_dochazka = {id_present}) / COUNT(*),
                       4
                   )                                   AS participace
            FROM `byl_pritomen` bp
            JOIN `clen`    c ON c.id = bp.ck_clen
            JOIN `zasedani` z ON z.id = bp.ck_zasedani
            WHERE z.ck_zastupitelstvo = {int(term)}
            GROUP BY bp.ck_clen, c.jmeno, c.prijmeni, c.obcanstvi
            ORDER BY participace DESC
        """)
        return result if isinstance(result, list) else []

    # ================================================================== #
    # Loyalty index                                                        #
    # ================================================================== #

    def mep_loyalty(self, term: int) -> List[Dict[str, Any]]:
        """
        Loyalty index per MEP × party for *term*.

        L = votes_matching_party_plurality / total_votes_cast

        Computed entirely in SQL via _tmp_mep_votes — no large pandas DataFrame.
        Party plurality uses window MAX to find the most-voted option per
        (vote × party); ties broken by lowest option ID.

        Returns list of {ck_clen, jmeno, prijmeni, obcanstvi,
                          ck_subjekt, zkratka, barva,
                          total, loyal_votes, loyalty}.
        """
        ProgressPrint.log(f"Computing MEP loyalty for term {term}…", "INFO")
        self._load_lookups()
        self._ensure_term_tables(term)

        rows = self._db.query("""
            SELECT mv.ck_clen,
                   mv.ck_subjekt,
                   COUNT(*)                                          AS total,
                   SUM(mv.ck_moznost = pl.plurality)                AS loyal_votes,
                   ROUND(SUM(mv.ck_moznost = pl.plurality) / COUNT(*), 4) AS loyalty
            FROM `_tmp_mep_votes` mv
            JOIN (
                SELECT ck_hlasovani, ck_subjekt, MIN(ck_moznost) AS plurality
                FROM (
                    SELECT ck_hlasovani, ck_subjekt, ck_moznost,
                           cnt,
                           MAX(cnt) OVER (PARTITION BY ck_hlasovani, ck_subjekt) AS max_cnt
                    FROM (
                        SELECT ck_hlasovani, ck_subjekt, ck_moznost,
                               COUNT(*) AS cnt
                        FROM `_tmp_mep_votes`
                        GROUP BY ck_hlasovani, ck_subjekt, ck_moznost
                    ) agg
                ) windowed
                WHERE cnt = max_cnt
                GROUP BY ck_hlasovani, ck_subjekt
            ) pl ON pl.ck_hlasovani = mv.ck_hlasovani
                AND pl.ck_subjekt   = mv.ck_subjekt
            GROUP BY mv.ck_clen, mv.ck_subjekt
            ORDER BY loyalty DESC
        """)

        if not isinstance(rows, list) or not rows:
            return []

        clen_map = self._mep_map()
        ps_map = self._party_map()
        records: List[Dict[str, Any]] = []
        for r in rows:
            c = clen_map.get(int(r["ck_clen"]), {})
            p = ps_map.get(int(r["ck_subjekt"]), {})
            records.append({
                "ck_clen":     int(r["ck_clen"]),
                "jmeno":       c.get("jmeno"),
                "prijmeni":    c.get("prijmeni"),
                "obcanstvi":   c.get("obcanstvi"),
                "ck_subjekt":  int(r["ck_subjekt"]),
                "zkratka":     p.get("zkratka"),
                "barva":       p.get("barva"),
                "color":       self._resolve_color(p.get("barva"), p.get("zkratka") or str(r["ck_subjekt"])),
                "total":       int(r["total"]),
                "loyal_votes": int(r["loyal_votes"]),
                "loyalty":     float(r["loyalty"]),
            })
        return records

    def government_opposition_loyalty(self, term: int) -> List[Dict[str, Any]]:
        """
        Loyalty to EP-group plurality for *term*, split by whether the MEP's
        national party was in government or in opposition domestically when
        each vote was cast.

        Same L = votes_matching_group_plurality / total_votes formula as
        mep_loyalty, but additionally grouped by (ck_national, in_government).
        Plurality is computed from the full EP-group membership (_tmp_mep_votes),
        not the government/opposition subset, so it reflects deviation from the
        group's actual overall position, not a position redefined within one
        bucket.

        See `_ensure_government_table` for the government-period matching
        logic and its coverage caveat (ve_vlade only covers each country's two
        most recent elections; a party with no matching row defaults to
        "opposition", which is a data-coverage limitation, not a verified
        fact, for periods outside that coverage).

        Returns list of {ck_clen, jmeno, prijmeni, obcanstvi,
                          ck_subjekt, zkratka, barva, color,
                          ck_national, national_zkratka, national_barva, national_color,
                          in_government, total, loyal_votes, loyalty}.
        """
        ProgressPrint.log(f"Computing government/opposition loyalty for term {term}…", "INFO")
        self._load_lookups()
        self._ensure_government_table(term)

        rows = self._db.query("""
            SELECT g.ck_clen,
                   g.ck_ep_group   AS ck_subjekt,
                   g.ck_national,
                   g.in_government,
                   COUNT(*)                                            AS total,
                   SUM(g.ck_moznost = pl.plurality)                    AS loyal_votes,
                   ROUND(SUM(g.ck_moznost = pl.plurality) / COUNT(*), 4) AS loyalty
            FROM `_tmp_mep_votes_gov` g
            JOIN (
                SELECT ck_hlasovani, ck_subjekt, MIN(ck_moznost) AS plurality
                FROM (
                    SELECT ck_hlasovani, ck_subjekt, ck_moznost, cnt,
                           MAX(cnt) OVER (PARTITION BY ck_hlasovani, ck_subjekt) AS max_cnt
                    FROM (
                        SELECT ck_hlasovani, ck_subjekt, ck_moznost, COUNT(*) AS cnt
                        FROM `_tmp_mep_votes`
                        GROUP BY ck_hlasovani, ck_subjekt, ck_moznost
                    ) agg
                ) windowed
                WHERE cnt = max_cnt
                GROUP BY ck_hlasovani, ck_subjekt
            ) pl ON pl.ck_hlasovani = g.ck_hlasovani AND pl.ck_subjekt = g.ck_ep_group
            GROUP BY g.ck_clen, g.ck_ep_group, g.ck_national, g.in_government
            ORDER BY g.ck_ep_group, g.in_government DESC, loyalty DESC
        """)

        if not isinstance(rows, list) or not rows:
            return []

        clen_map = self._mep_map()
        ps_map = self._party_map()  # covers both POLITICAL_GROUP and NATIONAL_POLITICAL_GROUP ids
        records: List[Dict[str, Any]] = []
        for r in rows:
            c = clen_map.get(int(r["ck_clen"]), {})
            p = ps_map.get(int(r["ck_subjekt"]), {})
            np_ = ps_map.get(int(r["ck_national"]), {})
            records.append({
                "ck_clen":          int(r["ck_clen"]),
                "jmeno":            c.get("jmeno"),
                "prijmeni":         c.get("prijmeni"),
                "obcanstvi":        c.get("obcanstvi"),
                "ck_subjekt":       int(r["ck_subjekt"]),
                "zkratka":          p.get("zkratka"),
                "barva":            p.get("barva"),
                "color":            self._resolve_color(p.get("barva"), p.get("zkratka") or str(r["ck_subjekt"])),
                "ck_national":      int(r["ck_national"]),
                "national_zkratka": np_.get("zkratka"),
                "national_barva":   np_.get("barva"),
                "national_color":   self._resolve_color(np_.get("barva"), np_.get("zkratka") or str(r["ck_national"])),
                "in_government":    bool(r["in_government"]),
                "total":            int(r["total"]),
                "loyal_votes":      int(r["loyal_votes"]),
                "loyalty":          float(r["loyalty"]),
            })
        return records

    # ================================================================== #
    # MEP comparison                                                       #
    # ================================================================== #

    def compare_meps(
        self,
        mep1_id: int,
        mep2_id: int,
        term: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Pairwise voting agreement between two MEPs.

        Compares all valid votes where both MEPs cast an active (yes/no/abstain)
        vote.  Optionally scoped to a single parliamentary term.

        Returns {total, shoda, neshoda, shoda_pct, hlasovani[]}.
        Each hlasovani entry: {ck_hlasovani, predmet, cas, cislo_zasedani,
                               moznost_mep1, moznost_mep2, shoda}.
        """
        self._load_lookups()
        active = self._active_ids()
        term_filter = (
            f"AND z.ck_zastupitelstvo = {int(term)}" if term is not None else ""
        )

        result = self._db.query(f"""
            SELECT m1.ck_hlasovani,
                   m1.ck_moznost AS moznost_mep1,
                   m2.ck_moznost AS moznost_mep2,
                   h.predmet, h.cas,
                   z.cislo AS cislo_zasedani
            FROM `hlasovani_clena` m1
            JOIN `hlasovani_clena` m2
                ON  m2.ck_hlasovani = m1.ck_hlasovani
                AND m2.ck_clen      = {int(mep2_id)}
            JOIN `hlasovani` h ON h.id = m1.ck_hlasovani
            JOIN `zasedani`  z ON z.id = h.ck_zasedani
            WHERE m1.ck_clen         = {int(mep1_id)}
              AND h.validni IS NOT FALSE
              AND m1.ck_moznost IN ({active})
              AND m2.ck_moznost IN ({active})
              {term_filter}
            ORDER BY h.cas
        """)

        if not isinstance(result, list) or not result:
            return {
                "total": 0, "shoda": 0, "neshoda": 0,
                "shoda_pct": 0.0, "hlasovani": [],
            }

        total = len(result)
        shoda = sum(1 for r in result if r["moznost_mep1"] == r["moznost_mep2"])
        for r in result:
            r["shoda"] = r["moznost_mep1"] == r["moznost_mep2"]
            r["cas"] = str(r["cas"])

        return {
            "total":     total,
            "shoda":     shoda,
            "neshoda":   total - shoda,
            "shoda_pct": round(shoda / total * 100, 2) if total else 0.0,
            "hlasovani": result,
        }

    # ================================================================== #
    # Correlation with socioeconomic indicators                            #
    # ================================================================== #

    def correlate_categories_with_indicators(self, term: int) -> Dict[str, object]:
        """
        For every hlasovani_kategorie (thematic label), compute country-level
        yes-fraction (final, non-procedural votes only) and correlate with every
        socioeconomic indicator stored in `ukazatele`.

        Indicator values are averaged over the years of the parliamentary term.

        Returns {
          "term": int,
          "categories": {
            "<KATEGORIE>": {
              "label": str,
              "vote_count": int,
              "indicators": {
                "<INDICATOR>": {
                  "pearson_r": float|None,
                  "p_value":   float|None,
                  "n_countries": int,
                  "points": [{country, vote_avg, indicator, total_votes}, …]
                }, …
              }
            }, …
          }
        }
        """
        from EUVoteAnalyzer.core.common import THEMATIC_LABELS, EUROSTAT

        self._load_lookups()
        self._ensure_votes_table(term)
        yes = self._yes_id()
        active = self._active_ids()

        term_info = self._db.query(
            "SELECT YEAR(funkcni_obdobi_od) AS yr_od,"
            " YEAR(IFNULL(funkcni_obdobi_do, NOW())) AS yr_do"
            " FROM `zastupitelstvo` WHERE `poradi` = %(t)s LIMIT 1",
            {"t": term},
        )
        yr_filter = ""
        if isinstance(term_info, list) and term_info:
            yr_od = int(term_info[0]["yr_od"])
            yr_do = int(term_info[0]["yr_do"])
            yr_filter = f"AND `rok` BETWEEN {yr_od} AND {yr_do}"

        # Per (category, country): yes-fraction and total active votes
        cat_rows = self._db.query(f"""
            SELECT h.hlasovani_kategorie              AS kategorie,
                   c.obcanstvi                        AS country,
                   ROUND(AVG(hc.ck_moznost = {yes}), 4) AS yes_fraction,
                   COUNT(*)                           AS total_votes
            FROM `_tmp_votes` tv
            STRAIGHT_JOIN `hlasovani` h  ON h.id           = tv.id
            STRAIGHT_JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
            JOIN `clen` c ON c.id = hc.ck_clen
            WHERE h.finalni IS TRUE
              AND h.proceduralni IS NOT TRUE
              AND h.hlasovani_kategorie IS NOT NULL
              AND hc.ck_moznost IN ({active})
              AND c.obcanstvi IS NOT NULL
            GROUP BY h.hlasovani_kategorie, c.obcanstvi
        """)

        if not isinstance(cat_rows, list) or not cat_rows:
            return {"term": term, "categories": {}}

        # Total distinct final votes per category
        cat_totals = self._db.query(f"""
            SELECT h.hlasovani_kategorie AS kategorie,
                   COUNT(DISTINCT h.id) AS vote_count
            FROM `_tmp_votes` tv
            JOIN `hlasovani` h ON h.id = tv.id
            WHERE h.finalni IS TRUE
              AND h.proceduralni IS NOT TRUE
              AND h.hlasovani_kategorie IS NOT NULL
            GROUP BY h.hlasovani_kategorie
        """)
        vote_count_map: Dict[str, int] = (
            {str(r["kategorie"]): int(r["vote_count"]) for r in cat_totals}
            if isinstance(cat_totals, list) else {}
        )

        # clen.obcanstvi uses ISO alpha-3; ukazatele.zeme uses Eurostat alpha-2.
        # Note: Greece → EL (not GR) per Eurostat convention.
        _iso3_to_eurostat: Dict[str, str] = {
            "AUT": "AT", "BEL": "BE", "BGR": "BG", "CYP": "CY", "CZE": "CZ",
            "DEU": "DE", "DNK": "DK", "EST": "EE", "GRC": "EL", "ESP": "ES",
            "FIN": "FI", "FRA": "FR", "HRV": "HR", "HUN": "HU", "IRL": "IE",
            "ITA": "IT", "LTU": "LT", "LUX": "LU", "LVA": "LV", "MLT": "MT",
            "NLD": "NL", "POL": "PL", "PRT": "PT", "ROU": "RO", "SWE": "SE",
            "SVN": "SI", "SVK": "SK",
        }

        # Organise vote data: category → eurostat_country → {yes_fraction, total_votes}
        from collections import defaultdict
        cat_country: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        for r in cat_rows:
            kat = str(r["kategorie"])
            raw = str(r["country"])
            eurostat_code = _iso3_to_eurostat.get(raw, raw)
            cat_country[kat][eurostat_code] = {
                "yes_fraction": float(r["yes_fraction"]),
                "total_votes":  int(r["total_votes"]),
                "country_iso3": raw,
            }

        # Load all indicators scoped to term years
        ind_data: Dict[str, Dict[str, float]] = {}
        for ind_name in EUROSTAT:
            rows = self._db.query(
                f"SELECT `zeme`, AVG(`hodnota`) AS hodnota"
                f" FROM `ukazatele` WHERE `nazev` = %(n)s {yr_filter} GROUP BY `zeme`",
                {"n": ind_name},
            )
            if isinstance(rows, list):
                ind_data[ind_name] = {
                    str(r["zeme"]): float(r["hodnota"])
                    for r in rows if r["hodnota"] is not None
                }

        # Build output
        categories: Dict[str, Dict[str, Any]] = {}
        for kat, country_votes in cat_country.items():
            indicators: Dict[str, Dict[str, Any]] = {}
            for ind_name, ind_map in ind_data.items():
                points: List[Dict[str, Any]] = []
                for eurostat_code, vd in country_votes.items():
                    ind_val = ind_map.get(eurostat_code)
                    if ind_val is not None:
                        points.append({
                            "country":     vd.get("country_iso3", eurostat_code),
                            "country_eu":  eurostat_code,
                            "vote_avg":    vd["yes_fraction"],
                            "indicator":   ind_val,
                            "total_votes": vd["total_votes"],
                        })
                if len(points) >= 3:
                    xs = [p["vote_avg"]  for p in points]
                    ys = [p["indicator"] for p in points]
                    pearson_r: Any
                    p_value: Any
                    pearson_r, p_value = pearsonr(xs, ys)
                    indicators[ind_name] = {
                        "pearson_r":   round(float(pearson_r), 4),
                        "p_value":     round(float(p_value),   6),
                        "n_countries": len(points),
                        "points":      points,
                    }
                else:
                    indicators[ind_name] = {
                        "pearson_r":   None,
                        "p_value":     None,
                        "n_countries": len(points),
                        "points":      points,
                    }
            categories[kat] = {
                "label":      THEMATIC_LABELS.get(kat, kat),
                "vote_count": vote_count_map.get(kat, 0),
                "indicators": indicators,
            }

        return {"term": term, "categories": categories}

    # ================================================================== #
    # National party × topic profile                                       #
    # ================================================================== #

    def party_topic_profile(
        self,
        term: int,
        min_votes_per_topic: int = 5,
    ) -> Dict[str, Any]:
        """
        Per-national-party voting profile across the 15 thematic topic categories.

        For each (national_party, topic) pair with ≥ min_votes_per_topic active
        non-procedural votes, computes the fraction of YES votes (0–1).

        Returns {
          "parties": [
            { party_id, zkratka, barva, country, ep_group,
              topics: { code: {yes_frac: float, n_votes: int} } }
          ],
          "topics":       [ordered list of topic codes present in data],
          "topic_labels": { code: label },
        }
        """
        from EUVoteAnalyzer.core.common import THEMATIC_LABELS

        ProgressPrint.log(f"Building party topic profiles for term {term}…", "INFO")
        self._load_lookups()
        self._ensure_national_table(term)

        yes    = self._yes_id()
        active = self._active_ids()

        rows = self._db.query(f"""
            SELECT
                ps.id                                AS party_id,
                ps.zkratka                           AS zkratka,
                ps.barva                             AS barva,
                h.hlasovani_kategorie                AS topic,
                ROUND(AVG(hc.ck_moznost = {yes}), 4) AS yes_frac,
                COUNT(*)                             AS n_votes
            FROM `_tmp_votes` tv
            JOIN `hlasovani` h ON h.id = tv.id
            STRAIGHT_JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
            JOIN `_tmp_member_national` tmn
                ON  tmn.ck_zasedani = tv.ck_zasedani
                AND tmn.ck_clen     = hc.ck_clen
            JOIN `politicky_subjekt` ps ON ps.id = tmn.ck_subjekt
            WHERE hc.ck_moznost IN ({active})
              AND tv.proceduralni IS NOT TRUE
              AND h.hlasovani_kategorie IS NOT NULL
            GROUP BY ps.id, ps.zkratka, ps.barva, h.hlasovani_kategorie
            HAVING COUNT(*) >= {int(min_votes_per_topic)}
        """)

        if not isinstance(rows, list) or not rows:
            return {"parties": [], "topics": [], "topic_labels": THEMATIC_LABELS}

        # Steps 2-4: build national-party → EP-group mapping.
        # Source: actual voting MEPs from hlasovani_clena (not _tmp_member_national),
        # so election-day membership records are found regardless of which
        # ck_zastupitelstvo value _linkTerm assigned them.
        # Both membership lookups use term-date range overlap only — ck_zastupitelstvo
        # is intentionally ignored — and a concurrency guard ensures the national-party
        # and EP-group periods themselves overlap (prevents cross-joins when an MEP
        # changed parties mid-term).  When a national party's MEPs split across
        # multiple EP groups, the majority (most MEPs) wins.
        t = int(term)
        ep_map_rows = self._db.query(f"""
            SELECT
                ps_nat.id                      AS nat_party_id,
                ps_ep.zkratka                  AS ep_group,
                COUNT(DISTINCT voters.ck_clen) AS n_meps
            FROM (
                SELECT DISTINCT hc.ck_clen
                FROM `hlasovani_clena` hc
                JOIN `hlasovani` h ON h.id  = hc.ck_hlasovani
                JOIN `zasedani`  z  ON z.id  = h.ck_zasedani
                WHERE z.ck_zastupitelstvo = {t}
                  AND hc.ck_moznost IN ({active})
                  AND h.validni      IS NOT FALSE
                  AND h.proceduralni IS NOT TRUE
            ) voters
            JOIN `prislusi_k`        pk_nat ON pk_nat.ck_clen = voters.ck_clen
            JOIN `politicky_subjekt` ps_nat ON ps_nat.id  = pk_nat.ck_subjekt
                                            AND ps_nat.typ = 'NATIONAL_POLITICAL_GROUP'
            JOIN `prislusi_k`        pk_ep  ON pk_ep.ck_clen = voters.ck_clen
            JOIN `politicky_subjekt` ps_ep  ON ps_ep.id  = pk_ep.ck_subjekt
                                            AND ps_ep.typ = 'POLITICAL_GROUP'
            JOIN `zastupitelstvo`    zt     ON zt.poradi  = {t}
            WHERE pk_nat.datum_od <= IFNULL(zt.funkcni_obdobi_do,
                                            DATE_ADD(zt.funkcni_obdobi_od, INTERVAL 5 YEAR))
              AND IFNULL(pk_nat.datum_do, '9999-12-31') >= zt.funkcni_obdobi_od
              AND pk_ep.datum_od  <= IFNULL(zt.funkcni_obdobi_do,
                                            DATE_ADD(zt.funkcni_obdobi_od, INTERVAL 5 YEAR))
              AND IFNULL(pk_ep.datum_do,  '9999-12-31') >= zt.funkcni_obdobi_od
              AND pk_nat.datum_od <= IFNULL(pk_ep.datum_do,  '9999-12-31')
              AND pk_ep.datum_od  <= IFNULL(pk_nat.datum_do, '9999-12-31')
            GROUP BY ps_nat.id, ps_ep.zkratka
            ORDER BY n_meps DESC
        """)
        ep_map: Dict[int, str] = {}
        if isinstance(ep_map_rows, list):
            for r in ep_map_rows:
                pid = int(r["nat_party_id"])
                if pid not in ep_map:  # ORDER BY n_meps DESC → first = majority EP group
                    ep_map[pid] = str(r["ep_group"])

        # Country per national party — taken from politicky_subjekt.zeme, which
        # is the party's own country, not the MEP's personal nationality.
        country_rows = self._db.query("""
            SELECT ps.id    AS party_id,
                   ps.zeme  AS country
            FROM `politicky_subjekt` ps
            WHERE ps.typ = 'NATIONAL_POLITICAL_GROUP'
              AND ps.zeme IS NOT NULL
        """)
        country_map: Dict[int, str] = (
            {int(r["party_id"]): str(r["country"]) for r in country_rows}
            if isinstance(country_rows, list) else {}
        )

        # Pivot rows → party_id → {topic → data}
        party_data: Dict[int, Dict[str, Any]] = {}
        topic_set: Set[str] = set()
        for r in rows:
            pid   = int(r["party_id"])
            topic = str(r["topic"])
            topic_set.add(topic)
            if pid not in party_data:
                party_data[pid] = {
                    "party_id": pid,
                    "zkratka":  str(r["zkratka"]) if r["zkratka"] else str(pid),
                    "barva":    str(r["barva"])   if r["barva"]   else None,
                    "country":  country_map.get(pid),
                    "ep_group": ep_map.get(pid),
                    "topics":   {},
                }
            party_data[pid]["topics"][topic] = {
                "yes_frac": float(r["yes_frac"]),
                "n_votes":  int(r["n_votes"]),
            }

        ordered_topics = [k for k in THEMATIC_LABELS if k in topic_set]
        topic_labels   = {k: v for k, v in THEMATIC_LABELS.items() if k in topic_set}
        parties = sorted(party_data.values(), key=lambda p: (p["country"] or "", p["zkratka"]))

        _, color_by_zkr = self._ep_color_maps()
        ep_groups_seen = {p["ep_group"] for p in parties if p.get("ep_group")}
        ep_group_colors = {
            g: color_by_zkr.get(g, self._resolve_color(None, g)) for g in ep_groups_seen
        }

        ProgressPrint.log(
            f"  Parties: {len(parties)}, topics: {len(ordered_topics)}", "INFO"
        )
        return {
            "parties":         parties,
            "topics":          ordered_topics,
            "topic_labels":    topic_labels,
            "ep_group_colors": ep_group_colors,
        }

    @staticmethod
    def _assoc_rules(frequent: pd.DataFrame, min_confidence: float) -> List[Dict[str, Any]]:
        """
        Memory-efficient rule generator — replaces mlxtend.association_rules.

        mlxtend materialises all (antecedent, consequent) pairs as one large
        NumPy array, which OOMs even on small datasets when many itemsets exist.
        This iterates over itemsets one at a time and streams rules out.
        """
        support_map: Dict[frozenset[str], float] = {
            frozenset(row["itemsets"]): float(row["support"])
            for _, row in frequent.iterrows()
        }
        # best_for_itemset: keep only the highest-lift split per unique itemset.
        # A∧B→C, A∧C→B, B∧C→A are three orientations of the same pattern {A,B,C};
        # enumerating all of them inflates the rule count without adding information.
        best_for_itemset: Dict[frozenset[str], Dict[str, Any]] = {}
        for _, row in frequent.iterrows():
            itemset = frozenset(row["itemsets"])
            if len(itemset) < 2:
                continue
            itemset_sup = float(row["support"])
            for size in range(1, len(itemset)):
                for ant_tuple in _combinations(sorted(itemset), size):
                    ant      = frozenset(ant_tuple)
                    cons     = itemset - ant
                    ant_sup  = support_map.get(ant, 0.0)
                    if ant_sup == 0.0:
                        continue
                    confidence = itemset_sup / ant_sup
                    if confidence < min_confidence:
                        continue
                    cons_sup = support_map.get(cons, 0.0)
                    lift     = confidence / cons_sup if cons_sup > 0 else 0.0
                    candidate : Dict[str, Any] = {
                        "antecedents": ant,
                        "consequents": cons,
                        "support":     itemset_sup,
                        "confidence":  confidence,
                        "lift":        lift,
                    }
                    prev = best_for_itemset.get(itemset)
                    if prev is None or lift > prev["lift"]:
                        best_for_itemset[itemset] = candidate
        return sorted(best_for_itemset.values(), key=lambda r: r["lift"], reverse=True)

    # ================================================================== #
    # Topic deviation rules                                                #
    # ================================================================== #

    def topic_deviation_rules(
        self,
        term: int,
        deviation_threshold: float = 0.10,
        min_support: float = 0.20,
        min_confidence: float = 0.70,
        min_lift: float = 1.15,
        min_votes_per_topic: int = 5,
        max_itemset_len: int = 3,
    ) -> Dict[str, Any]:
        """
        Association rules among national parties based on how they deviate
        from their EP group's average position on each thematic topic.

        Transaction = one national party.
        Item = "{TOPIC}:ABOVE" if party yes_frac > ep_group_mean + threshold,
               "{TOPIC}:BELOW" if party yes_frac < ep_group_mean - threshold.
        Topics near the group mean produce no item (filtered out as uninformative).

        This reveals cross-topic ideological patterns: e.g. parties that are
        more pro-environment than their group also tend to be more pro-labour.

        Returns {
          "rules":          [{antecedents, consequents, support, confidence, lift}],
          "n_transactions": int,
          "n_topics":       int,
          "topic_labels":   {code: label},
          "ep_group_means": {group: {topic: mean_yes_frac}},
          "parameters":     {deviation_threshold, min_support, min_confidence, min_lift},
        }
        """
        from collections import defaultdict

        try:
            from mlxtend.frequent_patterns import fpgrowth #type: ignore
            from mlxtend.preprocessing import TransactionEncoder #type: ignore
        except ImportError:
            ProgressPrint.log("mlxtend not found — run: pip install mlxtend", "ERR!")
            return {"rules": [], "n_transactions": 0, "n_topics": 0,
                    "topic_labels": {}, "ep_group_means": {}, "group_colors": {},
                    "parameters": {"deviation_threshold": deviation_threshold,
                                   "min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        ProgressPrint.log(f"Computing topic deviation rules for term {term}…", "INFO")

        profile = self.party_topic_profile(term, min_votes_per_topic)
        parties      = profile["parties"]
        topics       = profile["topics"]
        topic_labels = profile["topic_labels"]

        if not parties or not topics:
            return {"rules": [], "n_transactions": 0, "n_topics": 0,
                    "topic_labels": topic_labels, "ep_group_means": {}, "group_colors": {},
                    "parameters": {"deviation_threshold": deviation_threshold,
                                   "min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        # EP group means per topic
        def _nested_defaultdict() -> defaultdict[str, List[float]]:
            return defaultdict(list)
        group_topic_vals: defaultdict[str, defaultdict[str, List[float]]] = defaultdict(_nested_defaultdict)
        for p in parties:
            grp = p.get("ep_group")
            if not grp:
                continue
            for topic, td in p["topics"].items():
                group_topic_vals[grp][topic].append(float(td["yes_frac"]))
        group_means: Dict[str, Dict[str, float]] = {
            grp: {t: round(sum(v) / len(v), 4) for t, v in td.items()}
            for grp, td in group_topic_vals.items()
        }
        _, color_by_zkr = self._ep_color_maps()
        group_colors = {g: color_by_zkr.get(g, self._resolve_color(None, g)) for g in group_means}

        # Build transactions
        transactions: List[List[str]] = []
        for p in parties:
            grp = p.get("ep_group")
            if not grp or grp not in group_means:
                continue
            means = group_means[grp]
            items : List[str] = []
            for topic, td in p["topics"].items():
                mean = means.get(topic)
                if mean is None:
                    continue
                dev = float(td["yes_frac"]) - mean
                if dev > deviation_threshold:
                    items.append(f"{topic}:ABOVE")
                elif dev < -deviation_threshold:
                    items.append(f"{topic}:BELOW")
            if items:
                transactions.append(items)

        ProgressPrint.log(
            f"  Transactions: {len(transactions)}, deviation_threshold={deviation_threshold}",
            "INFO"
        )

        if not transactions:
            return {"rules": [], "n_transactions": 0, "n_topics": len(topics),
                    "topic_labels": topic_labels, "ep_group_means": group_means,
                    "group_colors": group_colors,
                    "parameters": {"deviation_threshold": deviation_threshold,
                                   "min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        te : Any = TransactionEncoder()
        te_arr : Any = te.fit(transactions).transform(transactions)
        df_te = pd.DataFrame(te_arr, columns=te.columns_)

        frequent : pd.DataFrame = cast(pd.DataFrame, fpgrowth(df_te, min_support=min_support, use_colnames=True,
                            max_len=max_itemset_len))
        if frequent.empty:
            ProgressPrint.log("No frequent itemsets — try lowering min_support.", "WARN")
            return {"rules": [], "n_transactions": len(transactions), "n_topics": len(topics),
                    "topic_labels": topic_labels, "ep_group_means": group_means,
                    "group_colors": group_colors,
                    "parameters": {"deviation_threshold": deviation_threshold,
                                   "min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        ProgressPrint.log(f"  Frequent itemsets: {len(frequent)}", "INFO")
        rules = [r for r in self._assoc_rules(frequent, min_confidence) if r["lift"] >= min_lift]
        ProgressPrint.log(f"  Rules after lift≥{min_lift} filter: {len(rules)}", "INFO")

        return {
            "rules": [
                {
                    "antecedents": sorted(r["antecedents"]),
                    "consequents": sorted(r["consequents"]),
                    "support":    round(r["support"],    4),
                    "confidence": round(r["confidence"], 4),
                    "lift":       round(r["lift"],        4),
                }
                for r in rules
            ],
            "n_transactions": len(transactions),
            "n_topics":       len(topics),
            "topic_labels":   topic_labels,
            "ep_group_means": group_means,
            "group_colors":   group_colors,
            "parameters": {
                "deviation_threshold": deviation_threshold,
                "min_support":         min_support,
                "min_confidence":      min_confidence,
                "min_lift":            min_lift,
                "max_itemset_len":     max_itemset_len,
            },
        }

    # ================================================================== #
    # Faction opposition rules                                             #
    # ================================================================== #

    def faction_opposition_rules(
        self,
        term: int,
        min_support: float = 0.30,
        min_confidence: float = 0.70,
        min_lift: float = 1.15,
    ) -> Dict[str, Any]:
        """
        Association rules for EP faction co-voting patterns on final votes.

        Transaction = one final non-procedural vote.
        Item = "{EP_GROUP}:FOR"     if faction's plurality vote was YES (+)
               "{EP_GROUP}:AGAINST" if faction's plurality vote was NO  (-)
               "{EP_GROUP}:ABSTAIN" if faction's plurality vote was abstain (0)

        Rules reveal coalition dynamics: e.g. "PPE:FOR ∧ ECR:FOR → S&D:AGAINST"
        shows the right-wing majority vs centre-left opposition pattern.

        Returns {
          "rules":          [{antecedents, consequents, support, confidence, lift}],
          "n_transactions": int,
          "n_groups":       int,
          "groups":         [ep_group_abbr, ...],
          "parameters":     {min_support, min_confidence, min_lift},
        }
        """
        try:
            from mlxtend.frequent_patterns import fpgrowth #type: ignore
            from mlxtend.preprocessing import TransactionEncoder #type: ignore
        except ImportError:
            ProgressPrint.log("mlxtend not found — run: pip install mlxtend", "ERR!")
            return {"rules": [], "n_transactions": 0, "n_groups": 0, "groups": [],
                    "parameters": {"min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        ProgressPrint.log(f"Computing faction opposition rules for term {term}…", "INFO")
        self._load_lookups()
        self._ensure_term_tables(term)

        yes_id     = self._yes_id()
        no_id      = self._no_id()
        abstain_id = self._abstain_id()

        # Per (vote, EP group): count of each vote option — final votes only
        rows = self._db.query(f"""
            SELECT mv.ck_hlasovani  AS vote_id,
                   ps.zkratka       AS ep_group,
                   mv.ck_moznost    AS option_id,
                   COUNT(*)         AS n
            FROM `_tmp_mep_votes` mv
            JOIN `hlasovani` h  ON h.id  = mv.ck_hlasovani
            JOIN `politicky_subjekt` ps ON ps.id = mv.ck_subjekt
            WHERE h.finalni IS TRUE
            GROUP BY mv.ck_hlasovani, mv.ck_subjekt, mv.ck_moznost
        """)

        if not isinstance(rows, list) or not rows:
            return {"rules": [], "n_transactions": 0, "n_groups": 0, "groups": [],
                    "group_colors": {},
                    "parameters": {"min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        # Aggregate per (vote, group) → plurality direction
        from collections import defaultdict
        def _init_vote_counter() -> dict[int, int]:
            return {yes_id: 0, no_id: 0, abstain_id: 0}
        def _init_faction_map() -> defaultdict[str, dict[int, int]]:
            return defaultdict(_init_vote_counter)
        vote_group: defaultdict[int, defaultdict[str, dict[int, int]]] = defaultdict(_init_faction_map)
        for row in rows:
            vid  = int(row["vote_id"])
            grp  = str(row["ep_group"])
            opt  = int(row["option_id"])
            cnt  = int(row["n"])
            vote_group[vid][grp][opt] = vote_group[vid][grp].get(opt, 0) + cnt

        opt_label = {yes_id: "FOR", no_id: "AGAINST", abstain_id: "ABSTAIN"}

        transactions: List[List[str]] = []
        for vid, groups in vote_group.items():
            items : List[str] = []
            for grp, counts in groups.items():
                # plurality: option with most votes (ties: yes > no > abstain by id order)
                plurality = max(counts, key=lambda o: (counts[o], -o))
                label = opt_label.get(plurality, "ABSTAIN")
                items.append(f"{grp}:{label}")
            if len(items) >= 3:
                transactions.append(items)

        all_groups = sorted({item.split(":")[0] for t in transactions for item in t})
        ProgressPrint.log(
            f"  Transactions (final votes): {len(transactions)}, "
            f"EP groups: {len(all_groups)}", "INFO"
        )
        _, color_by_zkr = self._ep_color_maps()
        group_colors = {g: color_by_zkr.get(g, self._resolve_color(None, g)) for g in all_groups}

        if not transactions:
            return {"rules": [], "n_transactions": 0, "n_groups": 0, "groups": all_groups,
                    "group_colors": group_colors,
                    "parameters": {"min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        te : Any = TransactionEncoder()
        te_arr = te.fit(transactions).transform(transactions)
        df_te  = pd.DataFrame(te_arr, columns=te.columns_)

        frequent : pd.DataFrame = cast(pd.DataFrame, fpgrowth(df_te, min_support=min_support, use_colnames=True, max_len=3))
        if frequent.empty:
            ProgressPrint.log("No frequent itemsets — try lowering min_support.", "WARN")
            return {"rules": [], "n_transactions": len(transactions),
                    "n_groups": len(all_groups), "groups": all_groups,
                    "group_colors": group_colors,
                    "parameters": {"min_support": min_support, "min_confidence": min_confidence,
                                   "min_lift": min_lift}}

        ProgressPrint.log(f"  Frequent itemsets: {len(frequent)}", "INFO")
        rules = [r for r in self._assoc_rules(frequent, min_confidence) if r["lift"] >= min_lift]
        ProgressPrint.log(f"  Rules after lift≥{min_lift} filter: {len(rules)}", "INFO")

        return {
            "rules": [
                {
                    "antecedents": sorted(r["antecedents"]),
                    "consequents": sorted(r["consequents"]),
                    "support":    round(r["support"],    4),
                    "confidence": round(r["confidence"], 4),
                    "lift":       round(r["lift"],        4),
                }
                for r in rules
            ],
            "n_transactions": len(transactions),
            "n_groups":       len(all_groups),
            "groups":         all_groups,
            "group_colors":   group_colors,
            "parameters": {
                "min_support":    min_support,
                "min_confidence": min_confidence,
                "min_lift":       min_lift,
            },
        }

    # ================================================================== #
    # Batch anomaly export                                                 #
    # ================================================================== #

    def export_all_mep_anomalies(
        self,
        term: int,
        output_dir: str,
        final_only: bool = True,
    ) -> int:
        """
        Precompute anomalous votes for every MEP in *term* and write
        one JSON file per MEP to output_dir/anomalous_votes/{mep_id}.json.

        Uses two bulk queries against _tmp_mep_votes (built by
        _ensure_term_tables) so that the 14 M-row hlasovani_clena
        table is scanned only twice regardless of MEP count.

        Returns the number of MEP files written.
        """
        import json
        import os
        from itertools import groupby

        self._load_lookups()
        self._ensure_term_tables(term)

        subdir = os.path.join(output_dir, "anomalous_votes")
        os.makedirs(subdir, exist_ok=True)

        final_filter = "AND h.finalni IS TRUE" if final_only else ""

        ProgressPrint.log(
            f"Loading MEP votes for term {term}"
            f"{' (final only)' if final_only else ''}…",
            "INFO",
        )

        # One full scan: all MEP votes with timestamps and categories
        mep_rows = self._db.query(f"""
            SELECT mv.ck_clen, mv.ck_hlasovani, mv.ck_moznost, mv.ck_subjekt,
                   h.cas, h.predmet, h.hlasovani_kategorie
            FROM `_tmp_mep_votes` mv
            JOIN `hlasovani` h ON h.id = mv.ck_hlasovani
            WHERE 1=1 {final_filter}
            ORDER BY mv.ck_clen, h.cas
        """)

        if not isinstance(mep_rows, list) or not mep_rows:
            ProgressPrint.log("No MEP votes found — nothing to export.", "WARN")
            return 0

        ProgressPrint.log(f"  {len(mep_rows)} vote rows loaded.", "INFO")

        # Faction plurality per (vote, EP-group) — same final filter
        ProgressPrint.log("Computing faction plurality…", "INFO")
        faction_rows = self._db.query(f"""
            SELECT mv.ck_hlasovani, mv.ck_subjekt, mv.ck_moznost,
                   COUNT(*) AS vote_count
            FROM `_tmp_mep_votes` mv
            JOIN `hlasovani` h ON h.id = mv.ck_hlasovani
            WHERE 1=1 {final_filter}
            GROUP BY mv.ck_hlasovani, mv.ck_subjekt, mv.ck_moznost
        """)

        plurality_map: Dict[Tuple[int, int], int] = {}
        if isinstance(faction_rows, list) and faction_rows:
            fv_df = pd.DataFrame(faction_rows)
            for col in ("ck_hlasovani", "ck_subjekt", "ck_moznost", "vote_count"):
                fv_df[col] = pd.to_numeric(fv_df[col])
            pl_df = self._plurality(fv_df)
            for _, pr in pl_df.iterrows():
                plurality_map[(int(pr["ck_hlasovani"]), int(pr["ck_subjekt"]))] = int(pr["plurality"])

        # All MEPs who have any vote in the term (regardless of finalni), so
        # we can write an empty result for those with no final votes — otherwise
        # the frontend would 404 and show "not precomputed" for them.
        all_mep_rows = self._db.query(
            "SELECT DISTINCT ck_clen FROM `_tmp_mep_votes`"
        )
        all_mep_ids = {int(r["ck_clen"]) for r in all_mep_rows} if isinstance(all_mep_rows, list) else set[int]()

        MIN_HISTORY = 5
        n_meps = 0
        written_ids: Set[int] = set()

        for mep_id_raw, group in groupby(mep_rows, key=lambda r: r["ck_clen"]):
            mep_id = int(mep_id_raw)
            votes = list(group)

            option_counts: Dict[int, int] = {}
            anomalous : List[Dict[str, Any]] = []

            for vote in votes:
                v_id   = int(vote["ck_hlasovani"])
                option = int(vote["ck_moznost"])
                subj   = int(vote["ck_subjekt"])

                running_majority = None
                if sum(option_counts.values()) >= MIN_HISTORY:
                    max_cnt = max(option_counts.values())
                    for opt in (1, 2, 3):
                        if option_counts.get(opt, 0) == max_cnt:
                            running_majority = opt
                            break

                party_vote = plurality_map.get((v_id, subj))

                dev_history = running_majority is not None and option != running_majority
                dev_party   = party_vote   is not None and option != party_vote

                if dev_history and dev_party:
                    anomalous.append({
                        "ck_hlasovani":    v_id,
                        "predmet":         vote["predmet"],
                        "cas":             str(vote["cas"]),
                        "kategorie":       vote.get("hlasovani_kategorie"),
                        "mep_vote":        option,
                        "running_majority": running_majority,
                        "party_vote":      party_vote,
                    })

                option_counts[option] = option_counts.get(option, 0) + 1

            result : Dict[str, Any] = {
                "total":           len(votes),
                "anomalous_count": len(anomalous),
                "votes":           anomalous,
            }

            path = os.path.join(subdir, f"{mep_id}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, default=str)

            written_ids.add(mep_id)
            n_meps += 1
            if n_meps % 100 == 0:
                ProgressPrint.log(f"  {n_meps} MEPs processed…", "INFO")

        # Write empty results for MEPs who had no final votes so the frontend
        # never sees a 404 (which it misreads as "not precomputed").
        empty : Dict[str, Any]= {"total": 0, "anomalous_count": 0, "votes": []}
        n_empty = 0
        for mep_id in all_mep_ids - written_ids:
            path = os.path.join(subdir, f"{mep_id}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(empty, fh)
            n_empty += 1

        if n_empty:
            ProgressPrint.log(f"  {n_empty} MEPs had no final votes — wrote empty results.", "INFO")

        ProgressPrint.log(f"Done — {n_meps + n_empty} MEPs written to {subdir}", "OK")
        return n_meps + n_empty

    def export_all_mep_comparisons(
        self,
        term: int,
        output_dir: str,
        min_common: int = 10,
        max_pairs: int = 5000,
    ) -> int:
        """Precompute all-pairs MEP vote-agreement and write JSON files.

        Produces:
          output_dir/mep_comparison/index.json   – MEP metadata + category list
          output_dir/mep_comparison/overall.json – top max_pairs pairs overall
          output_dir/mep_comparison/cat_CODE.json – top max_pairs pairs per category

        Returns the total number of JSON files written.
        """
        import json
        import os

        self._load_lookups()
        self._ensure_national_table(term)

        subdir = os.path.join(output_dir, "mep_comparison")
        os.makedirs(subdir, exist_ok=True)

        # ── 1. Global MEP list (consistent ordering for accumulator matrices) ──
        mep_id_rows = self._db.query(
            "SELECT DISTINCT ck_clen FROM `_tmp_mep_votes` ORDER BY ck_clen"
        )
        if not isinstance(mep_id_rows, list) or not mep_id_rows:
            ProgressPrint.log("No votes found — nothing to export.", "WARN")
            return 0
        mep_ids: List[int] = [int(r["ck_clen"]) for r in mep_id_rows]
        n = len(mep_ids)
        mep_to_i: Dict[int, int] = {mid: i for i, mid in enumerate(mep_ids)}

        # ── 2. Build MEP metadata (EP group + national party) ────────────
        ProgressPrint.log("Loading MEP metadata…", "INFO")
        mep_rows = self._db.query(
            "SELECT `id`, `jmeno`, `prijmeni`, `obcanstvi` FROM `clen`"
        )
        mep_base = {int(r["id"]): r for r in mep_rows} if isinstance(mep_rows, list) else {}

        # Most-frequent EP group per MEP in this term (from _tmp_mep_votes)
        ep_rows = self._db.query("""
            SELECT mv.ck_clen, mv.ck_subjekt, COUNT(*) AS n
            FROM `_tmp_mep_votes` mv
            GROUP BY mv.ck_clen, mv.ck_subjekt
        """)
        ep_grp: Dict[int, int] = {}
        ep_cnt: Dict[int, int] = {}
        if isinstance(ep_rows, list):
            for r in ep_rows:
                mid, sid, cnt = int(r["ck_clen"]), int(r["ck_subjekt"]), int(r["n"])
                if cnt > ep_cnt.get(mid, -1):
                    ep_cnt[mid] = cnt
                    ep_grp[mid] = sid

        # Most-frequent national party per MEP in this term
        nat_rows = self._db.query("""
            SELECT mn.ck_clen, mn.ck_subjekt, COUNT(*) AS n
            FROM `_tmp_member_national` mn
            GROUP BY mn.ck_clen, mn.ck_subjekt
        """)
        nat_grp: Dict[int, int] = {}
        nat_cnt: Dict[int, int] = {}
        if isinstance(nat_rows, list):
            for r in nat_rows:
                mid, sid, cnt = int(r["ck_clen"]), int(r["ck_subjekt"]), int(r["n"])
                if cnt > nat_cnt.get(mid, -1):
                    nat_cnt[mid] = cnt
                    nat_grp[mid] = sid

        # Party info lookup (id → {zkratka, barva})
        party_rows = self._db.query(
            "SELECT `id`, `zkratka`, `barva` FROM `politicky_subjekt`"
        )
        party_info: Dict[int, Dict[str, Any]] = {}
        if isinstance(party_rows, list):
            for r in party_rows:
                party_info[int(r["id"])] = r

        mep_index: Dict[str, Dict[str, Any]] = {}
        for mid in mep_ids:
            base = mep_base.get(mid, {})
            ep_id = ep_grp.get(mid)
            nat_id = nat_grp.get(mid)
            ep_info = party_info.get(ep_id, {}) if ep_id else {}
            nat_info = party_info.get(nat_id, {}) if nat_id else {}
            mep_index[str(mid)] = {
                "id":        mid,
                "jmeno":     base.get("jmeno", ""),
                "prijmeni":  base.get("prijmeni", ""),
                "obcanstvi": base.get("obcanstvi", ""),
                "ep_group":  ep_info.get("zkratka", ""),
                "ep_color":  self._resolve_color(ep_info.get("barva"), ep_info.get("zkratka", str(ep_id or mid))),
                "nat_party": nat_info.get("zkratka", ""),
                "nat_color": self._resolve_color(nat_info.get("barva"), nat_info.get("zkratka", str(nat_id or mid))),
            }

        # ── 3. Chunked pairwise computation (avoids loading all rows at once) ──
        # Global accumulator matrices (n×n, int64) — small even for 720 MEPs (~8 MB each)
        CHUNK = 2000
        g_agree = np.zeros((n, n), dtype=np.int64)
        g_valid  = np.zeros((n, n), dtype=np.int64)
        # Per-category accumulators, allocated on first encounter
        cat_agree: Dict[str, np.ndarray] = {}
        cat_valid:  Dict[str, np.ndarray] = {}

        vote_id_rows = self._db.query(
            "SELECT `id` FROM `_tmp_votes` WHERE `proceduralni` IS NOT TRUE ORDER BY `id`"
        )
        all_vote_ids: List[int] = [int(r["id"]) for r in (vote_id_rows or [])]
        n_chunks = (len(all_vote_ids) + CHUNK - 1) // CHUNK
        ProgressPrint.log(
            f"  Processing {len(all_vote_ids)} votes in {n_chunks} chunks of {CHUNK}…", "INFO"
        )

        def _accumulate(sub_df: pd.DataFrame, ag: np.ndarray, va: np.ndarray) -> None:
            """Add sub-dataframe's pair stats into the (ag, va) accumulator matrices."""
            # Deduplicate: _tmp_mep_votes can have multiple rows per (mep, vote) when
            # a MEP has multiple party memberships — keep only one row per (mep, vote).
            sub_df = sub_df.drop_duplicates(subset=["vote", "mep"])
            pivot = sub_df.pivot_table(
                index="vote", columns="mep", values="opt", aggfunc="first"
            )
            local_meps = [int(c) for c in pivot.columns]
            valid_cols = [j for j, m in enumerate(local_meps) if m in mep_to_i]
            local_idx  = np.array([mep_to_i[local_meps[j]] for j in valid_cols])
            if len(local_idx) < 2:
                return
            mat = pivot.values[:, valid_cols].astype(np.float32)
            vf  = (~np.isnan(mat)).astype(np.float32)
            nb  = (vf.T @ vf).astype(np.int64)
            ag_sub = np.zeros((len(local_idx), len(local_idx)), dtype=np.float32)
            for opt in (1, 2, 3):
                m = (mat == float(opt)).astype(np.float32)
                ag_sub += m.T @ m
            ix = np.ix_(local_idx, local_idx)
            ag[ix] += ag_sub.astype(np.int64)
            va[ix] += nb

        for chunk_i in range(n_chunks):
            batch = all_vote_ids[chunk_i * CHUNK : (chunk_i + 1) * CHUNK]
            placeholders = ",".join(map(str, batch))
            chunk_rows = self._db.query(
                f"SELECT DISTINCT ck_clen AS mep, ck_hlasovani AS vote, ck_moznost AS opt, kat "
                f"FROM `_tmp_mep_votes` WHERE ck_hlasovani IN ({placeholders})"
            )
            if not chunk_rows:
                continue
            chunk_df = pd.DataFrame(chunk_rows)
            chunk_df["mep"]  = pd.to_numeric(chunk_df["mep"])
            chunk_df["vote"] = pd.to_numeric(chunk_df["vote"])
            chunk_df["opt"]  = pd.to_numeric(chunk_df["opt"])

            _accumulate(chunk_df, g_agree, g_valid)

            for cat_val, cat_sub in chunk_df.groupby("kat", dropna=True):
                cat_key = str(cat_val)
                if cat_key not in cat_agree:
                    cat_agree[cat_key] = np.zeros((n, n), dtype=np.int64)
                    cat_valid[cat_key]  = np.zeros((n, n), dtype=np.int64)
                _accumulate(cat_sub, cat_agree[cat_key], cat_valid[cat_key])

            ProgressPrint.log(
                f"  chunk {chunk_i + 1}/{n_chunks} done"
                f" ({min(100, (chunk_i + 1) * 100 // n_chunks)}%)", "INFO"
            )

        # ── 4. Build ranked pairs from accumulator matrices ──────────────
        def _pairs_from_matrices(ag: np.ndarray, va: np.ndarray) -> List[Dict[str, Any]]:
            r_idx, c_idx = np.triu_indices(n, k=1)
            totals = va[r_idx, c_idx]
            agrees = ag[r_idx, c_idx]
            mask   = totals >= min_common
            r_idx, c_idx, totals, agrees = (
                r_idx[mask], c_idx[mask], totals[mask], agrees[mask]
            )
            if len(totals) == 0:
                return []
            pcts = agrees.astype(np.float64) / totals
            # Sort by rounded pct (1 decimal, matching display) so that pairs
            # showing the same percentage are ordered by common votes descending.
            # Raw float sort would rank 263/263=1.0 above 11094/11095≈0.9999
            # even though both display as "100%".
            pcts_sort = np.round(pcts * 10).astype(np.int64)  # e.g. 1000 = 100.0%
            order = np.lexsort((-totals, -pcts_sort))[:max_pairs]
            r_idx, c_idx, totals, agrees, pcts = (
                r_idx[order], c_idx[order], totals[order], agrees[order], pcts[order]
            )
            return [
                {
                    "mep1":  mep_ids[int(r_idx[i])],
                    "mep2":  mep_ids[int(c_idx[i])],
                    "agree": int(agrees[i]),
                    "total": int(totals[i]),
                    "pct":   round(float(pcts[i]) * 100, 1),
                }
                for i in range(len(r_idx))
            ]

        ProgressPrint.log("Computing overall pairwise agreement…", "INFO")
        overall_pairs = _pairs_from_matrices(g_agree, g_valid)
        ProgressPrint.log(f"  {len(overall_pairs)} overall pairs.", "INFO")

        categories: List[str] = sorted(cat_agree.keys())
        ProgressPrint.log(f"  {len(categories)} categories found.", "INFO")

        cat_pairs: Dict[str, List[Dict[str, Any]]] = {}
        for cat in categories:
            pairs = _pairs_from_matrices(cat_agree[cat], cat_valid[cat])
            if pairs:
                cat_pairs[cat] = pairs
            ProgressPrint.log(f"  cat '{cat}': {len(cat_pairs.get(cat, []))} pairs.", "INFO")

        # ── 6. Write JSON files ──────────────────────────────────────────
        n_files = 0

        index_data : Dict[str, Any] = {
            "meps":       mep_index,
            "categories": [c for c in categories if c in cat_pairs],
        }
        with open(os.path.join(subdir, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(index_data, fh, ensure_ascii=False)
        n_files += 1

        with open(os.path.join(subdir, "overall.json"), "w", encoding="utf-8") as fh:
            json.dump(overall_pairs, fh, ensure_ascii=False)
        n_files += 1

        for cat, pairs in cat_pairs.items():
            safe = cat.replace("/", "_").replace(" ", "_")
            with open(os.path.join(subdir, f"cat_{safe}.json"), "w", encoding="utf-8") as fh:
                json.dump(pairs, fh, ensure_ascii=False)
            n_files += 1

        ProgressPrint.log(f"Done — {n_files} files written to {subdir}", "OK")
        return n_files
