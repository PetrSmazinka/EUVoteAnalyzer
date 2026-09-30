"""
Data-import pipeline for loading external CSV dumps into the database.

AutoImport – abstract base class mirroring AutoFetch's two-phase _load / _process contract.
HtvImport  – imports the HowTheyVote.eu dataset (votes.csv + member_votes.csv), resolving
             MEP, session, and document references and classifying votes by topic via LLM.
"""

import os
import pandas as pd
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, cast

from EUVoteAnalyzer.core.common import THEMATIC_LABELS, VOTE_OPTIONS, STORED_QUERIES, DEBUG, ENDPOINTS
from EUVoteAnalyzer.core.llm import SmartLLM
from EUVoteAnalyzer.core.utils import ProgressPrint
from EUVoteAnalyzer.database.orm import DB, DBObject, DBObjects


class AutoImport(ABC):
    """
    Abstract base class for two-phase data-import tasks.

    Subclasses implement :meth:`_load` (file/network ingestion) and
    :meth:`_process` (DB persistence).  :meth:`do` orchestrates both phases.
    """

    def __init__(self, process_immediately: bool = True):
        """
        Args:
            process_immediately: When ``True`` (default), :meth:`_process` is
                                 called automatically after a successful :meth:`_load`.
        """
        self._process_immediately = process_immediately

    @abstractmethod
    def _load(self, *args: Any, **kwargs: Any) -> bool:
        """Load source data into memory.

        Returns:
            ``True`` if enough data was loaded to proceed with processing.
        """
        return True

    @abstractmethod
    def _process(self, *args: Any, **kwargs: Any) -> None:
        """Persist the loaded data to the database."""
        pass

    def do(self, *args: Any, **kwargs: Any) -> None:
        """Run the full load → process pipeline, forwarding all arguments to both phases."""
        if self._load(*args, **kwargs):
            if self._process_immediately:
                self._process(*args, **kwargs)


class HtvImport(AutoImport):
    """Import votes and member votes from HowTheyVote.eu CSV files (votes.csv + member_votes.csv)."""

    _POSITION_MAP: Dict[str, str] = {
        "FOR": "+",
        "AGAINST": "-",
        "ABSTENTION": "0",
    }

    def __init__(self, process_immediately: bool = True):
        """Initialise caches for session and document ID lookups."""
        super().__init__(process_immediately)
        self._session_cache: Dict[str, int] = {}
        self._dokument_cache: Dict[str, int] = {}
        self._votes_df: pd.DataFrame = pd.DataFrame()
        self._member_votes_df: pd.DataFrame = pd.DataFrame()

    def _linkTerm(self, date_str: str) -> Optional[int]:
        """
        Resolve a date string to the parliamentary term (``zastupitelstvo.poradi``) it falls in.

        Args:
            date_str: Date in ``YYYY-MM-DD`` format.

        Returns:
            The matching term ordinal, or ``None`` if no term covers the date.
        """
        result = DB().query(
            f"SELECT `poradi` FROM `zastupitelstvo` WHERE '{date_str}' >= `funkcni_obdobi_od` "
            f"AND ('{date_str}' <= `funkcni_obdobi_do` OR `funkcni_obdobi_do` IS NULL) LIMIT 1"
        )
        if not isinstance(result, list) or len(result) == 0:
            return None
        return cast(int, result[0]['poradi'])

    def _linkSession(self, date_str: str) -> Optional[int]:
        """
        Return the ``zasedani.id`` for the given date, creating a stub session if absent.

        Results are cached in ``_session_cache`` to avoid repeated DB round-trips.

        Args:
            date_str: Date in ``YYYY-MM-DD`` format.

        Returns:
            The session's primary key, or ``None`` on failure.
        """
        if date_str in self._session_cache:
            return self._session_cache[date_str]
        result = DB().query(
            "SELECT `id` FROM `zasedani` WHERE DATE(`datum_od`) = %(d)s LIMIT 1", {"d": date_str}
        )
        if isinstance(result, list) and len(result) > 0:
            sid = cast(int, result[0]['id'])
            self._session_cache[date_str] = sid
            return sid
        cast(DBObject, DBObjects.get("zasedani")).save({
            "ck_zastupitelstvo": self._linkTerm(date_str),
            "datum_od": f"{date_str} 00:00:00",
            "datum_do": f"{date_str} 23:59:59",
        })
        result = DB().query(
            "SELECT `id` FROM `zasedani` WHERE DATE(`datum_od`) = %(d)s LIMIT 1", {"d": date_str}
        )
        if isinstance(result, list) and len(result) > 0:
            sid = cast(int, result[0]['id'])
            self._session_cache[date_str] = sid
            return sid
        return None

    def _init_moznosti(self) -> Dict[str, int]:
        """Load (or seed) vote-option rows and return a ``{symbol → id}`` mapping."""
        result = DB().query("SELECT `id`, `text` FROM `hlasovaci_moznost`")
        moznosti: Dict[str, int] = {}
        if isinstance(result, list):
            for row in result:
                moznosti[str(row['text'])] = cast(int, row['id'])
        if not moznosti:
            moznost_table = cast(DBObject, DBObjects.get("hlasovaci_moznost"))
            for option in VOTE_OPTIONS:
                moznost_table.save(option)
            result = DB().query("SELECT `id`, `text` FROM `hlasovaci_moznost`")
            if isinstance(result, list):
                for row in result:
                    moznosti[str(row['text'])] = cast(int, row['id'])
        return moznosti

    def _init_vysledky(self) -> Dict[str, int]:
        """Load (or seed) vote-outcome rows and return a ``{label → id}`` mapping."""
        result = DB().query("SELECT `id`, `text` FROM `vysledek_hlasovani`")
        vysledky: Dict[str, int] = {}
        if isinstance(result, list):
            for row in result:
                vysledky[str(row['text'])] = cast(int, row['id'])
        vysledek_table = cast(DBObject, DBObjects.get("vysledek_hlasovani"))
        for label in ("ADOPTED", "REJECTED", "LAPSED"):
            if label not in vysledky:
                vysledek_table.save({"text": label})
        result = DB().query("SELECT `id`, `text` FROM `vysledek_hlasovani`")
        if isinstance(result, list):
            for row in result:
                vysledky[str(row['text'])] = cast(int, row['id'])
        return vysledky

    def _build_mep_cache(self) -> Dict[int, int]:
        """Return a ``{ep_id → clen.id}`` lookup for all MEPs stored in the database."""
        result = DB().query("SELECT `id`, `ep_id` FROM `clen` WHERE `ep_id` IS NOT NULL")
        if not isinstance(result, list) or not result:
            ProgressPrint.log("MEP cache is empty — no ep_id values found in clen", "WARN")
            return {}
        cache = {int(row["ep_id"]): int(row["id"]) for row in result}
        ProgressPrint.log(f"MEP cache built: {len(cache)} entries", "INFO")
        return cache

    def _load(self, votes_path: Optional[str] = None, member_votes_path: Optional[str] = None,
              *args: Any, **kwargs: Any) -> bool:
        """
        Load the HowTheyVote CSV files into in-memory DataFrames.

        Supports both local files and remote ``.csv.gz`` URLs.  When no path is
        provided the latest release is downloaded from the configured HTV endpoint.

        Args:
            votes_path:        Local path to ``votes.csv`` / ``votes.csv.gz``, or
                               ``None`` to download automatically.
            member_votes_path: Local path to ``member_votes.csv`` / ``.csv.gz``,
                               or ``None`` to download automatically.

        Returns:
            ``True`` if both files were loaded successfully.
        """
        votes_src        = votes_path        or f"{ENDPOINTS["HOW_THEY_VOTE"]}/votes.csv.gz"
        member_votes_src = member_votes_path or f"{ENDPOINTS["HOW_THEY_VOTE"]}/member_votes.csv.gz"

        is_remote_votes        = votes_path        is None
        is_remote_member_votes = member_votes_path is None

        if is_remote_votes:
            ProgressPrint.log(f"Downloading HowTheyVote votes from {votes_src}", "INFO")
        else:
            ProgressPrint.log(f"Loading HowTheyVote votes from {votes_src}", "INFO")
            if not os.path.exists(votes_src):
                ProgressPrint.log(f"Votes file not found: {votes_src}", "ERR!")
                return False

        if is_remote_member_votes:
            ProgressPrint.log(f"Downloading HowTheyVote member votes from {member_votes_src}", "INFO")
        else:
            ProgressPrint.log(f"Loading HowTheyVote member votes from {member_votes_src}", "INFO")
            if not os.path.exists(member_votes_src):
                ProgressPrint.log(f"Member votes file not found: {member_votes_src}", "ERR!")
                return False

        try:
            votes_kwargs : Dict[str, Any]= {"compression": "gzip"} if is_remote_votes or votes_src.endswith(".gz") else {}
            self._votes_df = cast(pd.DataFrame, pd.read_csv(votes_src, **votes_kwargs))
            ProgressPrint.log(f"Loaded {len(self._votes_df)} votes", "OK")

            ProgressPrint.log("Reading member votes (large file, this may take a while)", "WARN")
            mv_kwargs : Dict[str, Any]= {"compression": "gzip"} if is_remote_member_votes or member_votes_src.endswith(".gz") else {}
            self._member_votes_df = cast(pd.DataFrame, pd.read_csv(member_votes_src, **mv_kwargs))
            ProgressPrint.log(f"Loaded {len(self._member_votes_df)} member vote records", "OK")
        except Exception as e:
            ProgressPrint.log(f"Failed to load HowTheyVote data: {e}", "ERR!")
            return False

        return True

    def _linkDokument(self, nazev: str) -> Optional[int]:
        """
        Return the ``dokument.id`` for *nazev*, inserting a new row if needed.

        Results are cached in ``_dokument_cache``.

        Args:
            nazev: Document reference string (e.g. a procedure or texts-adopted reference).

        Returns:
            The document's primary key, or ``None`` on failure.
        """
        if nazev in self._dokument_cache:
            return self._dokument_cache[nazev]
        result = DB().query(
            "SELECT `id` FROM `dokument` WHERE `nazev` = %(n)s LIMIT 1", {"n": nazev}
        )
        if isinstance(result, list) and result:
            did = cast(int, result[0]['id'])
            self._dokument_cache[nazev] = did
            return did
        cast(DBObject, DBObjects.get("dokument")).save({"nazev": nazev})
        result = DB().query(
            "SELECT `id` FROM `dokument` WHERE `nazev` = %(n)s LIMIT 1", {"n": nazev}
        )
        if isinstance(result, list) and result:
            did = cast(int, result[0]['id'])
            self._dokument_cache[nazev] = did
            return did
        return None

    def _process(self, *args: Any, **kwargs: Any) -> None:
        ProgressPrint.initialize("Importing HowTheyVote data")
        self._session_cache = {}
        self._dokument_cache = {}
        moznosti  = self._init_moznosti()
        vysledky  = self._init_vysledky()
        mep_cache = self._build_mep_cache()
        hlasovani_table       = cast(DBObject, DBObjects.get("hlasovani"))
        hlasovani_clena_table = cast(DBObject, DBObjects.get("hlasovani_clena"))
        mv_grouped = self._member_votes_df.groupby("vote_id")
        total = len(self._votes_df)

        row: Any 
        for i, row in enumerate(self._votes_df.itertuples(index=False)):
            ProgressPrint.progress(i, total)

            vote_id    = int(row.id)
            timestamp  = str(row.timestamp) if pd.notna(row.timestamp) else ""
            date_str   = timestamp[:10] if timestamp else ""
            cas        = timestamp[:19].replace("T", " ") if timestamp else None
            title      = str(row.display_title) if pd.notna(row.display_title) else None
            count_for      = int(row.count_for)        if pd.notna(row.count_for)        else 0
            count_against  = int(row.count_against)    if pd.notna(row.count_against)    else 0
            count_abstain  = int(row.count_abstention) if pd.notna(row.count_abstention) else 0
            if count_for == 0 and count_against == 0 and count_abstain == 0:
                result_label = "LAPSED"
            elif count_for > count_against:
                result_label = "ADOPTED"
            else:
                result_label = "REJECTED"
            vysledek_id = vysledky.get(result_label)

            proc_ref    = str(row.procedure_reference)    if pd.notna(row.procedure_reference)    else None
            adopted_ref = str(row.texts_adopted_reference) if pd.notna(row.texts_adopted_reference) else None
            is_main     = str(row.is_main).strip().lower() == "true" if pd.notna(row.is_main) else False

            procedura_dok_id  = self._linkDokument(proc_ref)    if proc_ref    else None
            vysledek_dok_id   = self._linkDokument(adopted_ref)  if adopted_ref else None

            hlasovani_typ      = str(row.procedure_type)  if pd.notna(row.procedure_type)  else None
            proc_title         = str(row.procedure_title) if pd.notna(row.procedure_title) else None

            cache_path = f"llm/topics/{vote_id}"

            if (is_main and hlasovani_typ):
                raw_response = SmartLLM.generate(cache_path, STORED_QUERIES["THEMATIC_LABEL"] \
                    .replace("[PROCEDURE_TYPE]", hlasovani_typ) \
                    .replace("[PROCEDURE_TITLE]", proc_title if proc_title else ""))
                llm_response = raw_response if raw_response and raw_response in THEMATIC_LABELS.keys() else None
            else:
                llm_response = None
            hlasovani_kategorie = llm_response

            session_id = self._linkSession(date_str) if date_str else None

            if session_id is not None:
                existing = DB().query(
                    "SELECT `id` FROM `hlasovani` WHERE `cislo` = %(c)s AND `ck_zasedani` = %(s)s LIMIT 1",
                    {"c": str(vote_id), "s": session_id}
                )
            else:
                existing = DB().query(
                    "SELECT `id` FROM `hlasovani` WHERE `cislo` = %(c)s AND `ck_zasedani` IS NULL LIMIT 1",
                    {"c": str(vote_id)}
                )
            if isinstance(existing, list) and existing:
                continue

            hlasovani_table.save({
                "ck_zasedani":       session_id,
                "cislo":             str(vote_id),
                "cas":               cas,
                "predmet":           title,
                "ck_vysledek":       vysledek_id,
                "finalni":           is_main,
                "proceduralni":      proc_ref is None,
                "procedura_dokument": procedura_dok_id,
                "vysledek_dokument": vysledek_dok_id,
                "hlasovani_typ":      hlasovani_typ,
                "hlasovani_kategorie": hlasovani_kategorie,
                "sum_ano":           count_for,
                "sum_ne":            count_against,
                "sum_zdrzel_se":     count_abstain,
                "sum_nehlasoval":    int(row.count_did_not_vote) if pd.notna(row.count_did_not_vote) else 0,
            })

            if session_id is not None:
                hlasovani_result = DB().query(
                    "SELECT `id` FROM `hlasovani` WHERE `cislo` = %(c)s AND `ck_zasedani` = %(s)s LIMIT 1",
                    {"c": str(vote_id), "s": session_id}
                )
            else:
                hlasovani_result = DB().query(
                    "SELECT `id` FROM `hlasovani` WHERE `cislo` = %(c)s AND `ck_zasedani` IS NULL LIMIT 1",
                    {"c": str(vote_id)}
                )
            if not isinstance(hlasovani_result, list) or len(hlasovani_result) == 0:
                continue
            hlasovani_id = cast(int, hlasovani_result[0]['id'])

            if vote_id not in mv_grouped.groups:
                continue
            member_rows = mv_grouped.get_group(vote_id)
            member_votes: List[Dict[str, Any]] = []
            
            mv: Any 
            for mv in member_rows.itertuples(index=False):
                symbol = self._POSITION_MAP.get(str(mv.position))
                if symbol is None:
                    continue
                moznost_id = moznosti.get(symbol)
                if moznost_id is None:
                    continue
                clen_id = mep_cache.get(int(mv.member_id))
                if clen_id is None:
                    continue
                member_votes.append({
                    "ck_hlasovani": hlasovani_id,
                    "ck_clen":      clen_id,
                    "ck_moznost":   moznost_id,
                    "prezencne":    True,
                })
            hlasovani_clena_table.save_many(member_votes)
            if DEBUG:
                break

        ProgressPrint.finalize(total)