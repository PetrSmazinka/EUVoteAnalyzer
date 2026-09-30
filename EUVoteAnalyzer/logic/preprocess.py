"""
Preprocessing module for EU Parliament voting data.

Computes derived/aggregated statistics that would be too slow to compute
on-the-fly for every API request.  Run with --prepare (optionally scoped
to a single parliamentary term via --prepare-term).

Tables populated:
  byl_pritomen                 – per-MEP per-session attendance status
  zasedani.dochazka            – count of present members per session
  statistika_clen              – vote counts per MEP × party × option × term
  statistika_subjekt           – vote counts per party × option × term
  soudrznost_subjektu_zasedani – Hix party cohesion index per party × session

All heavy queries that touch hlasovani_clena (14M rows) first join against a
small _tmp_votes temp table (~5k-50k rows per term, PRIMARY KEY on hlasovani.id).
This lets MariaDB use the existing FK index on hlasovani_clena.ck_hlasovani to
fetch only the ~1.5M rows for the current term — no ALTER TABLE required.
"""

from typing import Dict, List, Optional

from EUVoteAnalyzer.database.orm import DB
from EUVoteAnalyzer.core.utils import ProgressPrint


class Preprocessor:
    """Precomputes all derived statistics needed for Phase 2 visualisations."""

    def __init__(self) -> None:
        self._db = DB()
        self._moznosti: Dict[str, int] = {}   # text -> id  ('+', '-', '0')
        self._dochazka: Dict[str, int] = {}   # text -> id

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def do(self, term: Optional[int] = None) -> None:
        """
        Run all preprocessing steps.

        Args:
            term: If given, restrict computation to this parliamentary term
                  (zastupitelstvo.poradi).  Useful for incremental updates
                  after importing a single term's data.
        """
        scope = f"term {term}" if term is not None else "all terms"
        ProgressPrint.log(f"Starting preprocessing ({scope})…", "INFO")

        self._load_lookups()
        self._ensure_schema()

        terms = [term] if term is not None else self._all_terms()

        for t in terms:
            ProgressPrint.log(f"Processing term {t}…", "INFO")
            self._build_votes_map(t)
            ProgressPrint.log(f"  Building member-party map…", "INFO")
            self._build_member_party_map(t)

            ProgressPrint.log(f"  Computing session attendance…", "INFO")
            self._compute_session_attendance(t)
            ProgressPrint.log(f"  Computing member statistics…", "INFO")
            self._compute_member_stats(t)
            ProgressPrint.log(f"  Computing party unity…", "INFO")
            self._compute_party_unity(t)

            self._drop_votes_map()
            self._drop_member_party_map()

        self._compute_party_stats(term)

        ProgressPrint.log("Preprocessing complete.", "OK")

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _load_lookups(self) -> None:
        """Cache vote-option and attendance IDs for use in SQL literals."""
        result = self._db.query("SELECT `id`, `text` FROM `hlasovaci_moznost`")
        if isinstance(result, list):
            for row in result:
                self._moznosti[str(row["text"])] = int(row["id"])

        result = self._db.query("SELECT `id`, `text` FROM `dochazka`")
        if isinstance(result, list):
            for row in result:
                self._dochazka[str(row["text"])] = int(row["id"])

    def _ensure_schema(self) -> None:
        """
        Add columns / tables that were introduced by this module but may be
        absent on databases created before the schema update.
        """
        # Seed dochazka lookup table so _compute_session_attendance has valid IDs
        # even when --votes was never run (HTV-only workflow).
        for text in ("přítomnost", "nepřítomnost", "omluvení"):
            exists = self._db.query(
                "SELECT `id` FROM `dochazka` WHERE `text` = %(t)s LIMIT 1", {"t": text}
            )
            if not isinstance(exists, list) or not exists:
                self._db.query(
                    "INSERT INTO `dochazka` (`text`) VALUES (%(t)s)", {"t": text}
                )
        result = self._db.query("SELECT `id`, `text` FROM `dochazka`")
        if isinstance(result, list):
            for row in result:
                self._dochazka[str(row["text"])] = int(row["id"])
        # statistika_clen.ck_subjekt
        if not self._column_exists("statistika_clen", "ck_subjekt"):
            ProgressPrint.log("ALTER: adding statistika_clen.ck_subjekt…", "INFO")
            self._db.query(
                "ALTER TABLE `statistika_clen` "
                "ADD COLUMN `ck_subjekt` INT DEFAULT NULL"
            )
            self._db.query(
                "ALTER TABLE `statistika_clen` "
                "ADD CONSTRAINT `fk_sc_subjekt` "
                "FOREIGN KEY (`ck_subjekt`) REFERENCES `politicky_subjekt` (`id`)"
            )

        # byl_pritomen: composite index for fast NOT EXISTS (ck_zasedani, ck_clen)
        if not self._index_exists("byl_pritomen", "idx_bp_zasedani_clen"):
            ProgressPrint.log("ALTER: adding byl_pritomen composite index…", "INFO")
            self._db.query(
                "ALTER TABLE `byl_pritomen` "
                "ADD INDEX `idx_bp_zasedani_clen` (`ck_zasedani`, `ck_clen`)"
            )

        # soudrznost_subjektu_zasedani (new table)
        self._db.query("""
            CREATE TABLE IF NOT EXISTS `soudrznost_subjektu_zasedani` (
                `id`            INT   NOT NULL AUTO_INCREMENT PRIMARY KEY,
                `ck_zasedani`   INT   NOT NULL,
                `ck_subjekt`    INT   NOT NULL,
                `sum_ano`       INT   NOT NULL DEFAULT 0,
                `sum_ne`        INT   NOT NULL DEFAULT 0,
                `sum_zdrzel_se` INT   NOT NULL DEFAULT 0,
                `soudrznost`    FLOAT DEFAULT NULL,
                INDEX `idx_ssz_zasedani` (`ck_zasedani`),
                INDEX `idx_ssz_subjekt`  (`ck_subjekt`),
                FOREIGN KEY (`ck_zasedani`) REFERENCES `zasedani` (`id`),
                FOREIGN KEY (`ck_subjekt`)  REFERENCES `politicky_subjekt` (`id`)
            )
        """)

    def _column_exists(self, table: str, column: str) -> bool:
        """
        Return ``True`` if *column* exists in *table* within the current database schema.

        Args:
            table:  Table name (case-insensitive).
            column: Column name to look up.
        """
        result = self._db.query(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %(t)s AND COLUMN_NAME = %(c)s",
            {"t": table, "c": column},
        )
        return isinstance(result, list) and len(result) > 0

    def _index_exists(self, table: str, index_name: str) -> bool:
        """
        Return ``True`` if an index named *index_name* exists on *table*.

        Args:
            table:      Table name.
            index_name: Index identifier to look up in INFORMATION_SCHEMA.
        """
        result = self._db.query(
            "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %(t)s AND INDEX_NAME = %(i)s",
            {"t": table, "i": index_name},
        )
        return isinstance(result, list) and len(result) > 0

    def _all_terms(self) -> List[int]:
        """Return a sorted list of all parliamentary term ordinals present in the ``zasedani`` table."""
        result = self._db.query(
            "SELECT DISTINCT `ck_zastupitelstvo` FROM `zasedani` ORDER BY `ck_zastupitelstvo`"
        )
        terms = [row["ck_zastupitelstvo"] for row in result] if isinstance(result, list) else []
        ProgressPrint.log(f"Terms found: {terms}", "INFO")
        return terms

    # ------------------------------------------------------------------
    # Temp-table helpers
    # ------------------------------------------------------------------

    def _build_votes_map(self, term: int) -> None:
        """
        _tmp_votes: valid hlasovani IDs for this term, with their session ID.

        Size: ~5k–50k rows (one per motion, not per member vote).
        Joining hlasovani_clena.ck_hlasovani against this PRIMARY KEY lets
        MariaDB use the existing FK index to fetch only ~1.5M rows for the
        term instead of scanning all 14M rows.
        """
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_votes`")
        self._db.query(f"""
            CREATE TEMPORARY TABLE `_tmp_votes` AS
            SELECT h.id, h.ck_zasedani
            FROM `hlasovani` h
            JOIN `zasedani` z ON h.ck_zasedani = z.id
            WHERE z.ck_zastupitelstvo = {int(term)}
              AND h.validni IS NOT FALSE
        """)
        self._db.query("ALTER TABLE `_tmp_votes` ADD PRIMARY KEY (`id`)")
        self._db.query("ALTER TABLE `_tmp_votes` ADD INDEX `idx_tv_zasedani` (`ck_zasedani`)")
        result = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_votes`")
        n = result[0]["n"] if isinstance(result, list) and result else 0
        ProgressPrint.log(f"  _tmp_votes: {n} valid votes for term {term}", "INFO")

    def _drop_votes_map(self) -> None:
        """Drop the ``_tmp_votes`` temporary table if it exists."""
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_votes`")

    def _build_member_party_map(self, term: int) -> None:
        """
        _tmp_member_party: (session, member) → party for this term.

        The date-range join on prislusi_k is done once here across small
        tables (sessions × members), so downstream queries use a fast equijoin.
        Note: we intentionally omit pk.ck_zastupitelstvo = z.ck_zastupitelstvo
        so that memberships imported under a different term's FK still match
        by date range (EP group memberships fetched via --meps may carry the
        latest term's FK even for earlier mandates).
        """
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_member_party`")
        self._db.query(f"""
            CREATE TEMPORARY TABLE `_tmp_member_party` AS
            SELECT DISTINCT z.id AS ck_zasedani, pk.ck_clen, pk.ck_subjekt
            FROM `zasedani` z
            JOIN `prislusi_k` pk
                ON  DATE(z.datum_od)
                    BETWEEN pk.datum_od AND IFNULL(pk.datum_do, '9999-12-31')
            JOIN `politicky_subjekt` ps
                ON  ps.id = pk.ck_subjekt
                AND ps.typ = 'POLITICAL_GROUP'
            WHERE z.ck_zastupitelstvo = {int(term)}
        """)
        self._db.query(
            "ALTER TABLE `_tmp_member_party` "
            "ADD INDEX `idx_mp_sess_clen` (`ck_zasedani`, `ck_clen`)"
        )
        result = self._db.query("SELECT COUNT(*) AS n FROM `_tmp_member_party`")
        n = result[0]["n"] if isinstance(result, list) and result else 0
        ProgressPrint.log(f"  _tmp_member_party: {n} rows for term {term}", "INFO")

    def _drop_member_party_map(self) -> None:
        """Drop the ``_tmp_member_party`` temporary table if it exists."""
        self._db.query("DROP TEMPORARY TABLE IF EXISTS `_tmp_member_party`")

    # ------------------------------------------------------------------
    # Step 1: session attendance (per term)
    # ------------------------------------------------------------------

    def _compute_session_attendance(self, term: int) -> None:
        """
        Derive byl_pritomen for sessions that have no records yet
        (data imported from dumps; VotesFetch already fills this for API data).
        Also updates zasedani.dochazka for the term.
        """
        id_present = self._dochazka.get("přítomnost", 1)
        id_absent  = self._dochazka.get("nepřítomnost", 3)

        # Present: MEPs who cast at least one vote, only for sessions with no
        # existing byl_pritomen records.
        self._db.query(f"""
            INSERT INTO `byl_pritomen` (ck_zasedani, ck_clen, ck_dochazka)
            SELECT DISTINCT tv.ck_zasedani, hc.ck_clen, {id_present}
            FROM `_tmp_votes` tv
            JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
            WHERE NOT EXISTS (
                SELECT 1 FROM `byl_pritomen` bp
                WHERE bp.ck_zasedani = tv.ck_zasedani
            )
        """)

        # Absent: members active at session date who cast no vote
        self._db.query(f"""
            INSERT INTO `byl_pritomen` (ck_zasedani, ck_clen, ck_dochazka)
            SELECT z.id, pk.ck_clen, {id_absent}
            FROM `zasedani` z
            JOIN `prislusi_k` pk
                ON  pk.ck_zastupitelstvo = z.ck_zastupitelstvo
                AND DATE(z.datum_od)
                    BETWEEN pk.datum_od AND IFNULL(pk.datum_do, '9999-12-31')
            WHERE z.ck_zastupitelstvo = {int(term)}
              AND NOT EXISTS (
                SELECT 1 FROM `byl_pritomen` bp
                WHERE bp.ck_zasedani = z.id AND bp.ck_clen = pk.ck_clen
              )
        """)

        # Update zasedani.dochazka for this term
        self._db.query(f"""
            UPDATE `zasedani` z
            SET z.dochazka = (
                SELECT COUNT(*) FROM `byl_pritomen` bp
                WHERE bp.ck_zasedani = z.id AND bp.ck_dochazka = {id_present}
            )
            WHERE z.ck_zastupitelstvo = {int(term)}
        """)

    # ------------------------------------------------------------------
    # Step 2: member vote statistics (per term)
    # ------------------------------------------------------------------

    def _compute_member_stats(self, term: int) -> None:
        """
        Populate statistika_clen: vote counts per MEP × party × option × term.
        """
        self._db.query(
            "DELETE FROM `statistika_clen` WHERE ck_zastupitelstvo = %(t)s",
            {"t": term},
        )
        self._db.query(f"""
            INSERT INTO `statistika_clen`
                (ck_zastupitelstvo, ck_clen, ck_subjekt, ck_moznost, `sum`)
            SELECT {int(term)}, hc.ck_clen, tmp.ck_subjekt, hc.ck_moznost, COUNT(*)
            FROM `_tmp_votes` tv
            JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
            LEFT JOIN `_tmp_member_party` tmp ON tmp.ck_zasedani = tv.ck_zasedani
                                              AND tmp.ck_clen = hc.ck_clen
            GROUP BY hc.ck_clen, tmp.ck_subjekt, hc.ck_moznost
        """)
        result = self._db.query(
            "SELECT COUNT(*) AS n FROM `statistika_clen` WHERE ck_zastupitelstvo = %(t)s",
            {"t": term},
        )
        n = result[0]["n"] if isinstance(result, list) and result else 0
        ProgressPrint.log(f"  statistika_clen rows for term {term}: {n}", "INFO")

    # ------------------------------------------------------------------
    # Step 3: party vote statistics (once, after all terms)
    # ------------------------------------------------------------------

    def _compute_party_stats(self, term: Optional[int]) -> None:
        """
        Populate statistika_subjekt by aggregating statistika_clen.
        Must run after all per-term _compute_member_stats calls.
        """
        ProgressPrint.log("Computing party statistics…", "INFO")

        if term is not None:
            self._db.query(
                "DELETE FROM `statistika_subjekt` WHERE ck_zastupitelstvo = %(t)s",
                {"t": term},
            )
            where = f"WHERE ck_zastupitelstvo = {int(term)}"
            nat_where = f"AND z.ck_zastupitelstvo = {int(term)}"
        else:
            self._db.query("TRUNCATE TABLE `statistika_subjekt`")
            where = ""
            nat_where = ""

        self._db.query(f"""
            INSERT INTO `statistika_subjekt`
                (ck_zastupitelstvo, ck_subjekt, ck_moznost, `sum`)
            SELECT ck_zastupitelstvo, ck_subjekt, ck_moznost, SUM(`sum`)
            FROM `statistika_clen`
            {where}
            GROUP BY ck_zastupitelstvo, ck_subjekt, ck_moznost
        """)

        # National parties are absent from statistika_clen (_tmp_member_party
        # maps members only to their EP group).  Compute their totals directly.
        id_ano    = self._moznosti.get("+")
        id_ne     = self._moznosti.get("-")
        id_zdrzel = self._moznosti.get("0")
        if all([id_ano, id_ne, id_zdrzel]):
            active_ids = f"{id_ano}, {id_ne}, {id_zdrzel}"
            self._db.query(f"""
                INSERT INTO `statistika_subjekt`
                    (ck_zastupitelstvo, ck_subjekt, ck_moznost, `sum`)
                SELECT z.ck_zastupitelstvo, pk.ck_subjekt, hc.ck_moznost, COUNT(*)
                FROM `hlasovani_clena` hc
                JOIN `hlasovani` h  ON  h.id = hc.ck_hlasovani
                                     AND h.validni IS NOT FALSE
                JOIN `zasedani` z   ON  z.id = h.ck_zasedani
                JOIN `prislusi_k` pk ON pk.ck_clen = hc.ck_clen
                    AND DATE(z.datum_od)
                        BETWEEN pk.datum_od AND IFNULL(pk.datum_do, '9999-12-31')
                JOIN `politicky_subjekt` ps ON ps.id = pk.ck_subjekt
                    AND ps.typ != 'POLITICAL_GROUP'
                WHERE hc.ck_moznost IN ({active_ids})
                  {nat_where}
                GROUP BY z.ck_zastupitelstvo, pk.ck_subjekt, hc.ck_moznost
            """)

        ProgressPrint.log("Party statistics computed.", "OK")

    # ------------------------------------------------------------------
    # Step 4: party unity / Hix index (per term)
    # ------------------------------------------------------------------

    def _compute_party_unity(self, term: int) -> None:
        """
        Populate soudrznost_subjektu_zasedani: Hix cohesion per party × session.

        Formula: (max(yes,no,abstain) - 0.5*(total-max)) / total * 100
        """
        id_ano    = self._moznosti.get("+")
        id_ne     = self._moznosti.get("-")
        id_zdrzel = self._moznosti.get("0")

        if not all([id_ano, id_ne, id_zdrzel]):
            ProgressPrint.log(
                "Vote option IDs not found – run an importer first, then re-run --prepare.",
                "WARN",
            )
            return

        self._db.query(f"""
            DELETE FROM `soudrznost_subjektu_zasedani` 
            WHERE ck_zasedani IN (SELECT ck_zasedani FROM `_tmp_votes`)
        """)

        active_ids = f"{id_ano}, {id_ne}, {id_zdrzel}"

        self._db.query(f"""
            INSERT INTO `soudrznost_subjektu_zasedani`
                (ck_zasedani, ck_subjekt, sum_ano, sum_ne, sum_zdrzel_se, soudrznost)
            SELECT 
                data.ck_zasedani, 
                data.ck_subjekt, 
                data.s_ano, data.s_ne, data.s_0,
                (GREATEST(data.s_ano, data.s_ne, data.s_0) - 0.5 * (data.total - GREATEST(data.s_ano, data.s_ne, data.s_0))) 
                / NULLIF(data.total, 0) * 100
            FROM (
                SELECT STRAIGHT_JOIN
                    tv.ck_zasedani,
                    tmp.ck_subjekt,
                    SUM(hc.ck_moznost = {id_ano}) AS s_ano,
                    SUM(hc.ck_moznost = {id_ne}) AS s_ne,
                    SUM(hc.ck_moznost = {id_zdrzel}) AS s_0,
                    COUNT(*) AS total
                FROM `_tmp_votes` tv
                JOIN `hlasovani_clena` hc ON hc.ck_hlasovani = tv.id
                JOIN `_tmp_member_party` tmp ON tmp.ck_zasedani = tv.ck_zasedani
                                             AND tmp.ck_clen = hc.ck_clen
                WHERE hc.ck_moznost IN ({active_ids})
                GROUP BY tv.ck_zasedani, tmp.ck_subjekt
            ) AS data
        """)