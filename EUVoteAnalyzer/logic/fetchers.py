"""
Data-collection pipeline: fetches, transforms, and persists EP Open Data into the database.

Each public class encapsulates one data-collection phase.  All classes extend
:class:`AutoFetch`, which separates the *fetch* step (HTTP retrieval / SPARQL) from
the *process* step (DB persistence).

AutoFetch     – abstract base; ``do()`` orchestrates ``_fetch()`` then ``_process()``.
TermsFetch    – parliamentary terms from the EP Corporate Bodies API.
PartiesFetch  – political groups and national parties, enriched via Wikidata SPARQL.
MepsFetch     – MEP biographies and group memberships.
VotesFetch    – plenary sessions, attendance records, and roll-call votes.
StatsFetch    – Eurostat socioeconomic time-series with gap-filling prediction.
NationalFetch – national-parliament composition scraped from Wikipedia via LLM parsing.
"""

import json
import numpy as np
import pandas as pd # type: ignore
import random
import re
import requests
import time
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
from scipy.stats import linregress # type: ignore
from typing import Any, Dict, List, Optional, Tuple, cast

from EUVoteAnalyzer.core.common import EntityList, HTMLExtractOutput, HTMLMode, ValueType, ENDPOINTS, EUROSTAT, EU_COUNTRIES, EU_COUNTRIES_NAMES, DEBUG, STORED_QUERIES, START_YEAR, END_YEAR, LANGUAGE, SPARQL_QUERIES, PARTIAL_LIMIT, LLM_RESPONSE_SCHEMAS
from EUVoteAnalyzer.core.llm import SmartLLM
from EUVoteAnalyzer.core.networking import EurostatLoader, URLBuilder, TextLoader, WikiLoader
from EUVoteAnalyzer.core.storage import LocalCache, SmartLoader, SmartSparqlQuery
from EUVoteAnalyzer.core.utils import ProgressPrint, basename, listbasename, is_female, ep_term, to_mysql_datetime

from EUVoteAnalyzer.database.orm import DBObject, DBObjects, DB
from EUVoteAnalyzer.logic.api_walker import APIWalker

class AutoFetch(ABC):
    """
    Abstract base class for two-phase data-collection tasks.

    Subclasses implement :meth:`_fetch` (retrieval) and :meth:`_process`
    (persistence).  :meth:`do` orchestrates the sequence and, when
    *process_immediately* is ``False``, allows the caller to defer processing.
    """

    def __init__(self, process_immediately : bool = True):
        """
        Args:
            process_immediately: When ``True`` (default), :meth:`_process` is
                                 called automatically after a successful :meth:`_fetch`.
        """
        self._process_immediately = process_immediately

    @abstractmethod
    def _fetch(self, *args : Any, **kwargs : Any) -> bool:
        """Retrieve data from an external source and store it on the instance.

        Returns:
            ``True`` if enough data was retrieved to proceed with processing.
        """
        return True

    @abstractmethod
    def _process(self, *args : Any, **kwargs : Any) -> None:
        """Persist the data retrieved by :meth:`_fetch` to the database."""
        pass

    def do(self, *args : Any, **kwargs : Any):
        """Run the full fetch → process pipeline, passing all arguments to both phases."""
        if self._fetch(*args, **kwargs):
            if self._process_immediately:
                self._process(*args, **kwargs)

class TermsFetch(AutoFetch):
    """Fetches parliamentary-term metadata from the EP Corporate Bodies API."""
    def _fetch(self, *args : Any, **kwargs : Any) -> bool:
        aw = APIWalker()
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("identifier"))
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("type"))
        aw.filter(lambda term: isinstance(term["type"], list) and "ParliamentaryTerm" in term["type"])
        aw.transform(lambda x: [term['identifier'] for term in x])
        response = aw.execute(URLBuilder(ENDPOINTS["CORPORATE_BODIES"]).query_param("body-classification", "EU_INSTITUTION"))
        if response is not None:
            self._terms_ids : List[str] = response
            return True
        return True
    
    def _process(self, *args : Any, **kwargs : Any) -> None:
        ProgressPrint.initialize("Updating stored terms")
        zastupitelstvo_table = cast(DBObject, DBObjects.get("zastupitelstvo"))
        total_terms = len(self._terms_ids)
        base_ub = URLBuilder(ENDPOINTS["CORPORATE_BODIES"]).path_param(":id")
        for i, term in enumerate(self._terms_ids):
            ProgressPrint.progress(i, total_terms)
            ub = base_ub.clone()
            ub.bind("id", term)
            aw = APIWalker()
            aw.select(ValueType.REQUIRED, URLBuilder().path_param("label"), "poradi")
            aw.select(ValueType.REQUIRED, URLBuilder().path_param("temporal").path_param("startDate"), "funkcni_obdobi_od")
            aw.select(ValueType.OPTIONAL, URLBuilder().path_param("temporal").path_param("endDate"), "funkcni_obdobi_do")
            response = aw.execute(ub)
            if response is None or len(response) == 0:
                ProgressPrint.log(f"Skipping term {term} due to missing data", "ERR!")
                continue
            response = response[0]
            existing = DB().query(
                "SELECT `poradi` FROM `zastupitelstvo` WHERE `funkcni_obdobi_od` = %(d)s LIMIT 1",
                {"d": response.get("funkcni_obdobi_od")}
            )
            if isinstance(existing, list) and len(existing) > 0:
                ProgressPrint.log(f"Term {term} already in DB, skipping", "INFO")
                continue
            zastupitelstvo_table.save(response)
            if DEBUG:
                break
        ProgressPrint.finalize(total_terms)
        return None

class PartiesFetch(AutoFetch):
    """
    Fetches political groups and national parties from the EP API, enriched with Wikidata.

    EP parliamentary groups (EU_POLITICAL_GROUP) are linked to Wikidata entities via a
    single batched SPARQL call that retrieves acronyms and hex colours.  National parties
    (NATIONAL_POLITICAL_GROUP) are stored without Wikidata enrichment to avoid unreliable
    matches.  Duplicate detection uses ``wiki_id`` first, then ``(zkratka, zeme)`` as a
    fallback, and EP API identifiers are tracked in a separate alias table.
    """
    def _fetch(self, *args : Any, **kwargs : Any) -> bool:
        self._parties_ids: List[Tuple[str, str]] = []  # (ep_api_id, typ)
        base_url = URLBuilder(ENDPOINTS["CORPORATE_BODIES"])
        for api_cls, typ in (
            ("NATIONAL_POLITICAL_GROUP", "NATIONAL_POLITICAL_GROUP"),
            ("EU_POLITICAL_GROUP",       "POLITICAL_GROUP"),
        ):
            aw = APIWalker()
            aw.select(ValueType.REQUIRED, URLBuilder().path_param("identifier"))
            aw.transform(lambda x: [p['identifier'] for p in x])
            response = aw.execute(base_url.clone().query_param("body-classification", api_cls))
            if response:
                existing = {e[0] for e in self._parties_ids}
                self._parties_ids.extend([(pid, typ) for pid in response if pid not in existing])
        return len(self._parties_ids) > 0

    def _ensure_ep_ids_table(self) -> None:
        """Create the ``politicky_subjekt_ep_ids`` alias table if it does not yet exist."""
        DB().query("""
            CREATE TABLE IF NOT EXISTS `politicky_subjekt_ep_ids` (
                `ep_id`      VARCHAR(50) NOT NULL PRIMARY KEY,
                `ck_subjekt` INT         NOT NULL,
                FOREIGN KEY (`ck_subjekt`) REFERENCES `politicky_subjekt` (`id`)
            )
        """)

    def _register_ep_id(self, ep_id: str, subjekt_id: int) -> None:
        """Insert a mapping from an EP API identifier to the canonical ``politicky_subjekt.id``."""
        DB().query(
            "INSERT IGNORE INTO `politicky_subjekt_ep_ids` (`ep_id`, `ck_subjekt`) VALUES (%(e)s, %(s)s)",
            {"e": ep_id, "s": subjekt_id},
        )

    def _process(self, *args : Any, **kwargs : Any) -> None:
        ProgressPrint.initialize("Updating stored parties")
        self._ensure_ep_ids_table()
        subjekty_table = cast(DBObject, DBObjects.get("politicky_subjekt"))
        total_parties = len(self._parties_ids)
        base_ub = URLBuilder(ENDPOINTS["CORPORATE_BODIES"]).path_param(":id")
        self._sparql = SmartSparqlQuery()
        self._local_cache : LocalCache|None = kwargs.get("local_cache")
        self._prefetch_ep_groups_wikidata()  # one batch SPARQL before the party loop
        for i, (party, typ) in enumerate(self._parties_ids):
            ProgressPrint.progress(i, total_parties)
            # Skip if this API id is already mapped
            alias_existing = DB().query(
                "SELECT `ck_subjekt` FROM `politicky_subjekt_ep_ids` WHERE `ep_id` = %(e)s LIMIT 1",
                {"e": party},
            )
            if isinstance(alias_existing, list) and alias_existing:
                ProgressPrint.log(f"Party {party} already mapped, skipping", "INFO")
                continue
            ub = base_ub.clone()
            ub.bind("id", party)
            aw = APIWalker()
            aw.select(ValueType.REQUIRED, URLBuilder().path_param("label"), "zkratka")
            aw.select_one_of(ValueType.REQUIRED, [URLBuilder().path_param("prefLabel").path_param(LANGUAGE), URLBuilder().path_param("label")], "nazev")
            if typ == "NATIONAL_POLITICAL_GROUP":
                aw.select(ValueType.REQUIRED, URLBuilder().path_param("represents").path_param("0"), "zeme", [basename])
            else:
                aw.select(ValueType.OPTIONAL, URLBuilder().path_param("represents").path_param("0"), "zeme", [basename])
            response = aw.execute(ub)
            if response is None or len(response) == 0:
                ProgressPrint.log(f"Skipping party {party} due to missing data", "ERR!")
                continue
            response = response[0]
            # Wikidata enrichment: EP factions use the precise EU_POLITICAL_GROUP
            # query (filtered by wdt:P31 wd:Q779079 + acronym match).
            # National parties skip Wikidata entirely — wrong matches are common
            # and the free-text POLITICAL_PARTY query is unreliable at scale.
            if typ == "POLITICAL_GROUP":
                linking = self._linkEpGroup(response["zkratka"])
            else:
                linking = None
            wiki_id = linking[0] if linking is not None else None
            barva   = linking[1] if linking is not None else None
            # Dedup: match by wiki_id first, then by (zkratka + zeme) if wiki_id is null
            canonical_id: Optional[int] = None
            if wiki_id is not None:
                row = DB().query(
                    "SELECT `id` FROM `politicky_subjekt` WHERE `wiki_id` = %(w)s LIMIT 1",
                    {"w": wiki_id},
                )
                if isinstance(row, list) and row:
                    canonical_id = int(row[0]["id"])
            if canonical_id is None:
                row = DB().query(
                    "SELECT `id` FROM `politicky_subjekt` "
                    "WHERE `zkratka` = %(z)s AND `zeme` <=> %(c)s LIMIT 1",
                    {"z": response.get("zkratka"), "c": response.get("zeme")},
                )
                if isinstance(row, list) and row:
                    canonical_id = int(row[0]["id"])
            if canonical_id is not None:
                # For EP groups: refresh wiki_id/barva on the existing row so
                # re-runs populate data that may have been missing or wrong.
                if typ == "POLITICAL_GROUP" and (wiki_id or barva):
                    DB().query(
                        "UPDATE `politicky_subjekt` "
                        "SET `wiki_id` = COALESCE(%(w)s, `wiki_id`), "
                        "    `barva`   = COALESCE(%(b)s, `barva`) "
                        "WHERE `id` = %(id)s",
                        {"w": wiki_id, "b": barva, "id": canonical_id},
                    )
                self._register_ep_id(party, canonical_id)
                ProgressPrint.log(f"Party {party} is alias of id {canonical_id}", "INFO")
                continue
            response["wiki_id"] = wiki_id
            response["barva"]   = barva
            response["ep_id"] = party
            response["typ"] = typ
            # new_id = subjekty_table.save(response) #TODO refactor
            subjekty_table.save(response)
            # Fetch the just-inserted id and register it in the alias table
            inserted = DB().query(
                "SELECT `id` FROM `politicky_subjekt` WHERE `ep_id` = %(e)s LIMIT 1",
                {"e": party},
            )
            if isinstance(inserted, list) and inserted:
                self._register_ep_id(party, int(inserted[0]["id"]))
            if DEBUG:
                break
        ProgressPrint.finalize(total_parties)
        return None
    
    def _linkParty(self, party_label: str, *args : Any, **kwargs : Any) -> Tuple[str, str|None]|None:
        """
        Look up a national party on Wikidata by its label.

        Args:
            party_label: Human-readable party name used as the SPARQL search term.

        Returns:
            ``(wiki_id, hex_color)`` tuple, or ``None`` if no match is found.
        """
        safe_label = re.sub(r'[\\/:*?"<>|]', '_', party_label)
        cache_key = f"sparql/party/{safe_label}"
        result = self._sparql.query(SPARQL_QUERIES["POLITICAL_PARTY"](party_label), cache_key)
        if not result:
            return None
        return (basename(result[0]['item']), result[0].get('color', None))

    def _prefetch_ep_groups_wikidata(self) -> None:
        """Fetch every EP parliamentary group from Wikidata in a single SPARQL call.

        Builds self._ep_group_wikidata: lowercase-acronym → (wiki_id, barva).
        One round-trip replaces the previous N-per-party queries that triggered
        Wikidata's 429 rate limit when processing long party lists.
        """
        result = self._sparql.query(
            SPARQL_QUERIES["EU_POLITICAL_GROUP_ALL"](""), "sparql/ep_groups_all"
        )
        self._ep_group_wikidata: Dict[str, Tuple[str, str | None]] = {}
        if not isinstance(result, list):
            ProgressPrint.log("Could not load EP groups from Wikidata", "WARN")
            return
        for row in result:
            acr = row.get("acronym", "").strip().lower()
            if not acr or "item" not in row:
                continue
            if acr in self._ep_group_wikidata:
                continue  # keep first match per acronym
            wiki_id = basename(row["item"])
            color_raw: str | None = row.get("color")
            if color_raw:
                color_raw = color_raw.lstrip("#")  # store without '#'; frontend adds it
            self._ep_group_wikidata[acr] = (wiki_id, color_raw or None)
        ProgressPrint.log(
            f"Loaded {len(self._ep_group_wikidata)} EP groups from Wikidata", "INFO"
        )

    def _linkEpGroup(self, acronym: str) -> Tuple[str, str | None] | None:
        """Look up a faction in the pre-fetched Wikidata dict — no SPARQL per party."""
        return self._ep_group_wikidata.get(acronym.strip().lower())

class MepsFetch(AutoFetch):
    """Fetches MEP biographies and political-group memberships from the EP People API."""

    def _linkTerm(self, term_label: str) -> int|None:
        """
        Resolve a date string to the parliamentary term (``zastupitelstvo.poradi``) it falls in.

        Args:
            term_label: Date string (``YYYY-MM-DD``) from a membership start-date field.

        Returns:
            The matching term's ordinal number, or ``None`` if no term covers the date.
        """
        db = DB()
        result = db.query(f"SELECT `poradi` FROM `zastupitelstvo` WHERE '{term_label}' >= `funkcni_obdobi_od` AND ('{term_label}' <= `funkcni_obdobi_do` OR `funkcni_obdobi_do` IS NULL) LIMIT 1")
        if not isinstance(result, list):
            return None
        return cast(int, result[0]['poradi'])

    def _fetch(self, *args : Any, **kwargs : Any) -> bool:
        aw = APIWalker()
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("identifier"))
        aw.transform(lambda x: [person['identifier'] for person in x])
        response = aw.execute(URLBuilder(ENDPOINTS["MEPS"]))
        if response is not None:
            self._meps_ids : List[str] = response
            return True
        return False

    def _process(self, local_cache: LocalCache, *args : Any, **kwargs : Any):
        ProgressPrint.initialize("Updating stored MEPs")
        zastupitele_table = cast(DBObject, DBObjects.get("clen"))
        prislusi_k_table  = cast(DBObject, DBObjects.get("prislusi_k"))
        total_meps = len(self._meps_ids)
        base_ub = URLBuilder(ENDPOINTS["MEPS"]).path_param(":id")
        party_rows = DB().query("SELECT `ep_id`, `ck_subjekt` FROM `politicky_subjekt_ep_ids`")
        party_ep_to_id: Dict[str, int] = {}
        if isinstance(party_rows, list):
            party_ep_to_id = {str(row["ep_id"]): int(row["ck_subjekt"]) for row in party_rows}
        for i, mep in enumerate(self._meps_ids):
            ub = base_ub.clone()
            ub.bind("id", mep)
            ProgressPrint.progress(i, total_meps)
            aw = APIWalker()
            aw.select(ValueType.REQUIRED, URLBuilder().path_param("identifier"), "ep_id")
            aw.select(ValueType.OPTIONAL, URLBuilder().path_param("givenName"), "jmeno")
            aw.select(ValueType.REQUIRED, URLBuilder().path_param("familyName"), "prijmeni")
            aw.select(ValueType.OPTIONAL, URLBuilder().path_param("bday"), "datum_narozeni")
            aw.select(ValueType.OPTIONAL, URLBuilder().path_param("img"), "foto", [basename])
            aw.select(ValueType.OPTIONAL, URLBuilder().path_param("citizenship"), "obcanstvi", [basename])
            aw.select(ValueType.OPTIONAL, URLBuilder().path_param("hasGender"), "pohlavi", [is_female])
            aw.select_recursive(
                ValueType.REQUIRED,
                URLBuilder().path_param("hasMembership"),
                APIWalker(). \
                    select_one_of(ValueType.REQUIRED, [
                        URLBuilder().path_param("notation_codictFunctionId"),
                        URLBuilder().path_param("notation_codictMandateId")
                    ], "id"). \
                    select(ValueType.OPTIONAL, URLBuilder().path_param("organization"), "ck_subjekt", [basename]). \
                    select(ValueType.OPTIONAL, URLBuilder().path_param("membershipClassification"), "klasifikace", [basename]). \
                    select(ValueType.OPTIONAL, URLBuilder().path_param("memberDuring").path_param("startDate"), "datum_od"). \
                    select(ValueType.OPTIONAL, URLBuilder().path_param("memberDuring").path_param("endDate"), "datum_do"). \
                    select(ValueType.REQUIRED, URLBuilder().path_param("memberDuring").path_param("startDate"), "ck_zastupitelstvo", [self._linkTerm]). \
                    filter(lambda x: x["klasifikace"] in ("NATIONAL_POLITICAL_GROUP", "EU_POLITICAL_GROUP")),
                "prislusi_k"
            )
            response = aw.execute(ub)
            if response is None or len(response) == 0:
                ProgressPrint.log(f"Skipping MEP {mep} due to missing data", "ERR!")
                continue
            response = response[0]

            # Get or create the clen record
            existing_mep = DB().query(
                "SELECT `id` FROM `clen` WHERE `ep_id` = %(e)s LIMIT 1", {"e": mep}
            )
            if isinstance(existing_mep, list) and existing_mep:
                mep_db_id = int(existing_mep[0]["id"])
            else:
                mep_data = {k: v for k, v in response.items() if k != "prislusi_k"}
                zastupitele_table.save(mep_data)
                inserted = DB().query(
                    "SELECT `id` FROM `clen` WHERE `ep_id` = %(e)s LIMIT 1", {"e": mep}
                )
                if not isinstance(inserted, list) or not inserted:
                    continue
                mep_db_id = int(inserted[0]["id"])

            # Save memberships for both new and existing MEPs
            for membership in response.get("prislusi_k", []):
                ep_party_id = membership.get("ck_subjekt")
                db_party_id = party_ep_to_id.get(str(ep_party_id)) if ep_party_id is not None else None
                if db_party_id is None:
                    continue
                datum_od = membership.get("datum_od")
                existing_m = DB().query(
                    "SELECT `id` FROM `prislusi_k` WHERE `ck_clen` = %(c)s AND `datum_od` = %(d)s AND `ck_subjekt` = %(s)s LIMIT 1",
                    {"c": mep_db_id, "d": datum_od, "s": db_party_id},
                )
                if isinstance(existing_m, list) and existing_m:
                    continue
                prislusi_k_table.save({
                    "ck_clen":           mep_db_id,
                    "ck_subjekt":        db_party_id,
                    "ck_zastupitelstvo": membership.get("ck_zastupitelstvo"),
                    "datum_od":          datum_od,
                    "datum_do":          membership.get("datum_do"),
                })

            if not aw.used_cache:
                time.sleep(random.uniform(2, 5))
            if DEBUG:
                break
        ProgressPrint.finalize(total_meps)

class VotesFetch(AutoFetch):
    """
    Fetches plenary sessions, attendance records, and roll-call votes from the EP Meetings API.

    Data is collected year-by-year in paginated requests.  Each session is processed
    inline during the fetch loop; the class builds a ``{ep_id → clen.id}`` lookup once
    at start to resolve MEP identifiers cheaply without per-row DB queries.

    The vote pipeline has three levels:
    1. ``_process()``        – persist a single session and its attendance.
    2. ``_process_votes()``  – retrieve all vote-result events for a session.
    3. ``_process_single_vote()`` – persist one roll-call vote and its member choices.
    """

    def _fetch(self, *args: Any, **kwargs: Any) -> bool:
        # Build ep_id (int) → clen.id lookup once so _process can resolve MEPs cheaply.
        mep_rows = DB().query("SELECT ep_id, id FROM clen")
        self._mep_ep_to_id: Dict[int, int] = {}
        if isinstance(mep_rows, list):
            for row in mep_rows:
                try:
                    self._mep_ep_to_id[int(row["ep_id"])] = int(row["id"])
                except (ValueError, TypeError):
                    pass
        ProgressPrint.log(f"MEP lookup cache: {len(self._mep_ep_to_id)} entries", "INFO")

        aw = APIWalker()
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("id"), "id", [basename])
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("activity_start_date"), "datum_od", [to_mysql_datetime])
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("activity_end_date"), "datum_do", [to_mysql_datetime])
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("had_excused_person"), "omluveni_poslanci", [listbasename])
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("had_participant_person"), "pritomni_poslanci", [listbasename])
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("parliamentary_term"), "ck_zastupitelstvo", [ep_term])
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("number_of_attendees"), "dochazka")
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("had_activity_type"), "typ", [basename])
        aw.filter(lambda x: x["typ"] == "PLENARY_SITTING")
        for year in range(START_YEAR, END_YEAR):
            if DEBUG:
                year = 2025
            gub = URLBuilder(ENDPOINTS["MEETINGS"])
            gub.query_param("year", year)
            gub.query_param("language", LANGUAGE)
            resultset_empty = False
            iteration = 0
            while not resultset_empty:
                ub = gub.clone()
                ub.query_param("limit", PARTIAL_LIMIT)
                ub.query_param("offset", iteration * PARTIAL_LIMIT)
                response = cast(List[Dict[str, Any]] | None, aw.execute(ub))
                if response is None:
                    ProgressPrint.log("No response", "ERR!")
                    break
                if len(response) == 0:
                    resultset_empty = True
                else:
                    for item in response:
                        self._data = item
                        self._process()
                iteration += 1
                if DEBUG:
                    break
            if DEBUG:
                break

        return True

    def _process(self, *args: Any, **kwargs: Any) -> None:
        zasedani_table    = cast(DBObject, DBObjects.get("zasedani"))
        byl_pritomen_table = cast(DBObject, DBObjects.get("byl_pritomen"))

        cislo = self._data.get("id")  # e.g. "MTG-PL-2025-01-20"
        if not cislo:
            ProgressPrint.log("Session has no id, skipping", "ERR!")
            return

        # Skip if this session is already stored (idempotent re-runs), but still
        # process its votes in case a previous run failed between session and votes.
        existing = DB().query(
            "SELECT id FROM zasedani WHERE cislo = %(c)s LIMIT 1",
            {"c": cislo},
        )
        if isinstance(existing, list) and existing:
            ProgressPrint.log(f"Session {cislo} already in DB, checking votes", "INFO")
            self._process_votes(int(existing[0]["id"]), cislo)
            return

        try:
            term = int(self._data.get("ck_zastupitelstvo", 0))
        except (ValueError, TypeError):
            term = 0

        zasedani_table.save({
            "ck_zastupitelstvo": term,
            "cislo":             cislo,
            "datum_od":          self._data.get("datum_od"),
            "datum_do":          self._data.get("datum_do"),
            "dochazka":          self._data.get("dochazka"),
        })

        inserted = DB().query(
            "SELECT id FROM zasedani WHERE cislo = %(c)s LIMIT 1",
            {"c": cislo},
        )
        if not isinstance(inserted, list) or not inserted:
            ProgressPrint.log(f"Could not retrieve id for session {cislo}", "ERR!")
            return
        zasedani_id = int(inserted[0]["id"])

        bp_rows: List[Dict[str, Any]] = []
        skipped = 0

        for ep_id_str in cast(List[int|str], self._data.get("omluveni_poslanci") or []):
            try:
                clen_id = self._mep_ep_to_id.get(int(ep_id_str))
            except (ValueError, TypeError):
                clen_id = None
            if clen_id is None:
                skipped += 1
                continue
            bp_rows.append({"ck_zasedani": zasedani_id, "ck_clen": clen_id, "ck_dochazka": 4})

        for ep_id_str in cast(List[int|str], self._data.get("pritomni_poslanci") or []):
            try:
                clen_id = self._mep_ep_to_id.get(int(ep_id_str))
            except (ValueError, TypeError):
                clen_id = None
            if clen_id is None:
                skipped += 1
                continue
            bp_rows.append({"ck_zasedani": zasedani_id, "ck_clen": clen_id, "ck_dochazka": 1})

        if bp_rows:
            byl_pritomen_table.save_many(bp_rows)
        if skipped:
            ProgressPrint.log(f"Session {cislo}: {skipped} MEPs not found in DB (not yet fetched?)", "WARN")
        ProgressPrint.log(f"Session {cislo}: saved {len(bp_rows)} attendance records", "OK")

        self._process_votes(zasedani_id, cislo)

    _OUTCOME_MAP: Dict[str, int] = {"ADOPTED": 1, "REJECTED": 2, "LAPSED": 3}

    def _process_votes(self, zasedani_id: int, meeting_cislo: str) -> None:
        """Fetch PLENARY_VOTE_RESULTS for a session, then store each roll-call vote."""
        ub = (URLBuilder(ENDPOINTS["MEETINGS"])
              .path_param(meeting_cislo)
              .path_param("vote-results"))
        ub.query_param("language", LANGUAGE)

        aw = APIWalker()
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("id"), "id", [basename])
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("consists_of"), "vote_ids", [listbasename])
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("had_activity_type"), "typ", [basename])
        aw.filter(lambda x: x["typ"] == "PLENARY_VOTE_RESULTS")

        vote_items = aw.execute(ub)
        if not vote_items:
            ProgressPrint.log(f"No vote-results for session {meeting_cislo}", "INFO")
            return

        hlasovani_table      = cast(DBObject, DBObjects.get("hlasovani"))
        hlasovani_clena_table = cast(DBObject, DBObjects.get("hlasovani_clena"))

        n_votes = 0
        for item in vote_items:
            vote_ids = cast(List[str], item.get("vote_ids") or [])
            last_idx = len(vote_ids) - 1
            for i, vote_id in enumerate(vote_ids):
                is_final = (i == last_idx)
                self._process_single_vote(zasedani_id, vote_id, is_final, hlasovani_table, hlasovani_clena_table)
                n_votes += 1

        ProgressPrint.log(f"Session {meeting_cislo}: {n_votes} roll-call votes processed", "OK")

    def _process_single_vote(
        self,
        zasedani_id: int,
        vote_id: str,
        is_final: bool,
        hlasovani_table: DBObject,
        hlasovani_clena_table: DBObject,
    ) -> None:
        """Fetch one PLENARY_OUTCOME event and store it as hlasovani + hlasovani_clena."""
        ub = URLBuilder(ENDPOINTS["EVENTS"]).path_param(vote_id)
        ub.query_param("language", LANGUAGE)

        aw = APIWalker()
        aw.select(ValueType.REQUIRED, URLBuilder().path_param("notation_votingId"), "cislo")
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("activity_start_date"), "cas", [to_mysql_datetime])
        aw.select_one_of(
            ValueType.OPTIONAL,
            [
                URLBuilder().path_param("referenceText").path_param(LANGUAGE),
                URLBuilder().path_param("activity_label").path_param(LANGUAGE),
            ],
            "predmet",
        )
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("decision_outcome"), "vysledek", [basename])
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("decision_method"),  "metoda",   [basename])
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("number_of_votes_favor"),       "sum_ano")
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("number_of_votes_against"),     "sum_ne")
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("number_of_votes_abstention"),  "sum_zdrzel_se")
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("number_of_attendees"),         "n_pritomnych")
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("had_voter_favor"),      "za",      [listbasename])
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("had_voter_against"),    "proti",   [listbasename])
        aw.select(ValueType.OPTIONAL, URLBuilder().path_param("had_voter_abstention"), "zdrzeni", [listbasename])

        response = aw.execute(ub)
        if not response:
            ProgressPrint.log(f"No data for vote event {vote_id}", "WARN")
            return
        vote = response[0]

        cislo = vote.get("cislo")
        if not cislo:
            ProgressPrint.log(f"Vote event {vote_id} has no notation_votingId, skipping", "WARN")
            return

        existing = DB().query(
            "SELECT id FROM hlasovani WHERE cislo = %(c)s AND ck_zasedani = %(s)s LIMIT 1",
            {"c": cislo, "s": zasedani_id},
        )
        if isinstance(existing, list) and existing:
            ProgressPrint.log(f"Vote {cislo} already in DB, skipping", "INFO")
            return

        sum_ano = int(vote.get("sum_ano") or 0)
        sum_ne  = int(vote.get("sum_ne")  or 0)
        sum_zdr = int(vote.get("sum_zdrzel_se") or 0)
        n_pr    = vote.get("n_pritomnych")
        sum_nehlasoval = (int(n_pr) - sum_ano - sum_ne - sum_zdr) if n_pr is not None else None

        hlasovani_table.save({
            "ck_zasedani":    zasedani_id,
            "cislo":          cislo,
            "cas":            vote.get("cas"),
            "predmet":        vote.get("predmet"),
            "ck_vysledek":    self._OUTCOME_MAP.get(vote.get("vysledek") or "", None),
            "sum_ano":        sum_ano,
            "sum_ne":         sum_ne,
            "sum_zdrzel_se":  sum_zdr,
            "sum_nehlasoval": sum_nehlasoval,
            "tajne":          vote.get("metoda") == "VOTE_SECRET",
            "finalni":        is_final,
        })

        inserted = DB().query(
            "SELECT id FROM hlasovani WHERE cislo = %(c)s AND ck_zasedani = %(s)s LIMIT 1",
            {"c": cislo, "s": zasedani_id},
        )
        if not isinstance(inserted, list) or not inserted:
            ProgressPrint.log(f"Could not retrieve id for vote {cislo}", "ERR!")
            return
        hlasovani_id = int(inserted[0]["id"])

        hc_rows: List[Dict[str, Any]] = []
        for ep_id_str, moznost in [
            *((s, 1) for s in cast(List[int], vote.get("za")      or [])),
            *((s, 2) for s in cast(List[int], vote.get("proti")   or [])),
            *((s, 3) for s in cast(List[int], vote.get("zdrzeni") or [])),
        ]:
            try:
                clen_id = self._mep_ep_to_id.get(int(ep_id_str))
            except (ValueError, TypeError):
                continue
            if clen_id:
                hc_rows.append({
                    "ck_hlasovani": hlasovani_id,
                    "ck_clen":      clen_id,
                    "ck_moznost":   moznost,
                    "prezencne":    True,
                })

        if hc_rows:
            hlasovani_clena_table.save_many(hc_rows)
        ProgressPrint.log(f"Vote {cislo}: {len(hc_rows)} member votes saved", "INFO")

        if not aw.used_cache:
            time.sleep(random.uniform(1, 3))

class StatsFetch(AutoFetch):
    """
    Fetches Eurostat socioeconomic indicator datasets and fills temporal gaps with predictions.

    Each configured indicator (see ``EUROSTAT`` in ``common.py``) is downloaded as a wide
    DataFrame, filtered to EU member states, and passed through a two-stage gap-filling
    procedure:

    * :meth:`_make_global_prediction` – fits a global regression model (``lin``, ``linlog``,
      or ``mean``) across all countries and years.
    * :meth:`_local_prediction`       – applies country-specific regression weighted by R²
      against the global model, with linear interpolation for mid-series gaps.

    All data (observed and predicted) is stored in the ``ukazatele`` table.
    """

    def _fetch(self, *args: Any, **kwargs: Any) -> bool:
        ProgressPrint.initialize("Retrieving Eurostat data")
        self._kontextualnidata_table = cast(DBObject, DBObjects.get("ukazatele"))
        total_markers = len(EUROSTAT)
        loader = SmartLoader(EurostatLoader())
        for i, (key, info) in enumerate(EUROSTAT.items()):
            ProgressPrint.progress(i, total_markers)
            dataset_code = str(info["dataset"])
            ProgressPrint.log(f"Fetching {key} ({dataset_code})", "INFO")
            df = cast(pd.DataFrame|None, loader.load(URLBuilder("eurostat").path_param(dataset_code)))
            if df is None:
                continue
            if df.empty:
                ProgressPrint.log(f"Dataset {key} ({dataset_code}) is empty", "WARN")
                continue
            df.columns = [c.replace('geo\\TIME_PERIOD', 'geo').replace('geo\\time', 'geo') for c in df.columns]
            if "filter" in info:
                filter_data = info.get("filter")
                if isinstance(filter_data, dict):
                    for col, value in filter_data.items():
                        if col in df.columns:
                            df = df[df[col] == value]
            df = df[df['geo'].isin(EU_COUNTRIES)]
            self._key :str = key
            self._dataset : pd.DataFrame = df
            self._prediction_method = str(info.get("prediction"))
            self._process(key, df)
            if DEBUG:
                break
        ProgressPrint.finalize(total_markers)
        return True
    
    def _process(self, *args: Any, **kwargs: Any) -> None:
        ProgressPrint.log(f"Saving data {self._key} to database", "INFO")
        self._df = self._dataset.melt(
            id_vars=['geo'], 
            var_name='year', 
            value_name='value'
        )
        self._df['year'] = pd.to_numeric(self._df['year'], errors='coerce')
        self._df = self._df.dropna(subset=['year'])
        self._df['year'] = self._df['year'].astype(int)
        self._df['prediction'] = self._df['value'].isna()
        self._make_global_prediction()
        self._df = self._df.groupby('geo', group_keys=False).apply(self._local_prediction)
        self._df = self._df[self._df['year'] >= 1990]
        self._df["name"] = self._key
        
        result : EntityList = []
        for _, row in self._df.iterrows():
            result.append({
                "nazev": row["name"],
                "rok": row["year"],
                "zeme": row["geo"],
                "hodnota": row["value"],
                "predikce": bool(row["prediction"])
            })

        for record in result:
            self._kontextualnidata_table.save(record)
        ProgressPrint.log(f"{self._key} data saved", "OK")
        return 
    
    def _make_global_prediction(self):
        """Fit a global regression model across all countries and store slope/intercept for reuse."""
        self._df_clean = self._df.dropna(subset=['value']).copy()
        if self._prediction_method == "linlog":
            df_pos = self._df_clean[self._df_clean['value'] > 0]
            x = df_pos['year'].values.astype(np.float64)
            y_log = np.log(df_pos['value'].values.astype(np.float64))
            res = linregress(x, y_log)
            res_any = cast(Any, res)
            self._global_slope = float(res_any.slope)
            self._global_intercept = float(res_any.intercept)
        elif self._prediction_method == "lin":
            x = self._df_clean['year'].values.astype(np.float64)
            y = self._df_clean['value'].values.astype(np.float64)
            res = linregress(x, y)
            res_any = cast(Any, res)
            self._global_slope = float(res_any.slope)
            self._global_intercept = float(res_any.intercept)
        elif self._prediction_method == "mean":
            self._global_mean = float(self._df_clean['value'].mean())
            # fallback
            self._global_slope = 0.0
            self._global_intercept = self._global_mean
    
    def _local_prediction(self, group: pd.DataFrame) -> pd.DataFrame:
        """
        Fill missing values for a single country using the configured prediction method.

        Args:
            group: DataFrame slice for one country (``geo`` key).

        Returns:
            The group with ``value`` and ``prediction`` columns updated in place.
        """
        mask = group['value'].isna()
        if self._prediction_method == "linlog":
            group = self._linlog_prediction(group, mask)
        elif self._prediction_method == "lin":
            group = self._lin_prediction(group, mask)
        elif self._prediction_method == "mean":
            group = self._mean_prediction(group, mask)
        group['value'] = pd.to_numeric(group['value'], errors='coerce')
        if self._prediction_method != "linlog":
            group['value'] = group['value'].clip(lower=0)
        group['value'] = group['value'].round(2)
        return group
    
    def _linlog_prediction(self, group: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
        """Apply log-linear (exponential) extrapolation/interpolation to missing values."""
        if group['value'].dropna().empty:
            years_float = group['year'].astype(float)
            group['value'] = np.exp(years_float * self._global_slope + self._global_intercept)
            return group
        country_data = group.dropna(subset=['value']).copy()
        country_data = country_data[country_data['value'] > 0]
        if len(country_data) > 1:
            x_local = country_data['year'].values.astype(np.float64)
            y_local_log = np.log(country_data['value'].values.astype(np.float64))
            res = linregress(x_local, y_local_log)
            res_any = cast(Any, res)
            slope = float(res_any.slope)
            r_value = float(res_any.rvalue)
            weight = max(0.5, float(r_value**2))
            final_slope = (slope * weight) + (self._global_slope * (1 - weight))
        else:
            final_slope = self._global_slope
        if mask.any():
            first_idx = cast(Any, group['value'].first_valid_index())
            last_idx = cast(Any, group['value'].last_valid_index())
            if first_idx is not None and last_idx is not None:
                first_year = float(cast(Any, group.at[first_idx, 'year']))
                last_year = float(cast(Any, group.at[last_idx, 'year']))
                past_mask = mask & (group['year'] < first_year)
                future_mask = mask & (group['year'] > last_year)
                gap_mask = mask & (group['year'] > first_year) & (group['year'] < last_year)
                if future_mask.any():
                    last_val_log = np.log(float(cast(Any, group.at[last_idx, 'value'])))
                    y_subset = group.loc[future_mask, 'year'].astype(float)
                    group.loc[future_mask, 'value'] = np.exp(y_subset * final_slope + (last_val_log - last_year * final_slope))
                if past_mask.any():
                    first_val_log = np.log(float(cast(Any, group.at[first_idx, 'value'])))
                    y_subset = group.loc[past_mask, 'year'].astype(float)
                    group.loc[past_mask, 'value'] = np.exp(y_subset * self._global_slope + (first_val_log - first_year * self._global_slope))
                if gap_mask.any():
                    group['value'] = group['value'].interpolate(method='linear')
            else:
                y_subset = group.loc[mask, 'year'].astype(float)
                group.loc[mask, 'value'] = np.exp(y_subset * self._global_slope + self._global_intercept)
        return group
    
    def _lin_prediction(self, group: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
        """Apply linear extrapolation to missing values, blending country and global trends."""
        if group['value'].dropna().empty:
            mean_val = float(self._df_clean['value'].mean())
            years_float = group['year'].astype(float)
            group['value'] = years_float * self._global_slope + (mean_val - years_float.mean() * self._global_slope)
        else:
            country_data = group.dropna(subset=['value']).copy()
            if len(country_data) > 1:
                x_local = country_data['year'].values.astype(np.float64)
                y_local = country_data['value'].values.astype(np.float64)
                res = linregress(x_local, y_local)
                res_any = cast(Any, res)
                slope = float(res_any.slope)
                r_value = float(res_any.rvalue)
                weight = max(0.5, float(r_value**2))
                final_slope = (slope * weight) + (self._global_slope * (1 - weight))
            else:
                final_slope = self._global_slope
            if mask.any():
                last_idx = cast(Any, group['value'].last_valid_index())
                if last_idx is not None:
                    last_val = float(cast(Any, group.at[last_idx, 'value']))
                    last_year = float(cast(Any, group.at[last_idx, 'year']))
                    y_subset = group.loc[mask, 'year'].astype(float)
                    group.loc[mask, 'value'] = y_subset * final_slope + (last_val - last_year * final_slope)
                else:
                    y_subset = group.loc[mask, 'year'].astype(float)
                    group.loc[mask, 'value'] = y_subset * self._global_slope + self._global_intercept
        return group

    def _mean_prediction(self, group: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
        """Fill missing values with the mean of the most recent five observed data points."""
        if mask.any():
            recent_data = group['value'].dropna().tail(5)
            if not recent_data.empty:
                fill_val = float(recent_data.mean())
            else:
                fill_val = float(self._df_clean['value'].mean())
            group.loc[mask, 'value'] = fill_val
            group['value'] = group['value'].interpolate(method='linear').fillna(fill_val)
        return group

class NationalFetch(AutoFetch):
    """
    Collects national-parliament election results via Wikipedia scraping and LLM parsing.

    The pipeline has three stages:
    1. A navbox Wikipedia page listing EU-country parliament URLs is scraped and parsed
       by the LLM to produce a ``{country → wiki_url}`` map.
    2. Each country's parliament page is fetched to extract election years and per-year
       Wikipedia URLs.  For each year, the election result page is scraped and the LLM
       extracts per-party seat counts.
    3. Party detail pages are fetched and the LLM extracts full name, abbreviation, and
       colour.  Parties are matched against the existing ``politicky_subjekt`` DB rows
       by Wikidata ID or name.

    All LLM calls use :class:`~EUVoteAnalyzer.core.llm.SmartLLM` and are cached on disk
    so re-runs replay from cache without hitting the Gemini API.
    """

    def get_wikidata_id(self, html_code: str) -> str | None:
        """
        Extract the Wikidata entity ID embedded in a Wikipedia page's JSON-LD metadata.

        Args:
            html_code: Raw HTML of a Wikipedia article page.

        Returns:
            The Wikidata ``Q`` identifier (e.g. ``"Q123456"``), or ``None`` if not found.
        """
        soup = BeautifulSoup(html_code, 'html.parser')
        scripts = soup.find_all('script', type='application/ld+json')
        for script in scripts:
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                wikidata_url = data.get('mainEntity') or data.get('sameAs')
                if wikidata_url and 'wikidata.org' in wikidata_url:
                    match = re.search(r'Q\d+', wikidata_url)
                    if match:
                        return match.group(0)
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        return None

    def _parse_llm(self, result: Any) -> Any:
        """Deserialise an LLM response string to a Python object if it is a string."""
        if isinstance(result, str):
            return json.loads(result)
        return result

    def _pre_fetch(self, tl: TextLoader, sl: SmartLoader) -> Optional[Dict[str, Any]]:
        """
        Download and LLM-parse the Wikipedia navbox to build a country → parliament-URL map.

        Also pre-fetches and caches each country's parliamentary-term list so that the
        main :meth:`_fetch` loop can read them from disk without re-querying Wikipedia.

        Args:
            tl: :class:`~EUVoteAnalyzer.core.networking.TextLoader` configured for navbox mode.
            sl: :class:`~EUVoteAnalyzer.core.storage.SmartLoader` wrapping *tl*.

        Returns:
            ``{country_name: wiki_page_slug}`` dict, or ``None`` on failure.
        """
        ProgressPrint.log("Fetching list of wiki pages of parliaments in EU countries", "INFO")
        ub = URLBuilder(ENDPOINTS["WIKI"]).path_param("Template:Elections_in_Europe")
        response = cast(str | None, sl.load(ub))
        if response is None:
            ProgressPrint.log("Couldn't download list from WIKI", "ERR!")
            return None
        llm_response = SmartLLM.generate(
            "llm/list_of_countries",
            STORED_QUERIES["DISCOVER_NATIONAL_PARLIAMENTS"].replace("[HTML_CODE]", response)
        )
        if not llm_response:
            ProgressPrint.log("Couldn't parse country list using LLM", "ERR!")
            return None
        country_urls = cast(Dict[str, Any], self._parse_llm(llm_response))
        ProgressPrint.log("List of wiki pages successfully retrieved", "OK")

        for country in EU_COUNTRIES_NAMES:
            if country not in country_urls:
                ProgressPrint.log(f"Skipping parliamentary data for {country} - URL missing", "ERR!")
                if DEBUG:
                    break
                continue
            ProgressPrint.log(f"Fetching parliamentary terms for {country}", "INFO")
            ub = URLBuilder(ENDPOINTS["WIKI"]).path_param(country_urls[country])
            response = cast(str | None, sl.load(ub))
            if response is None:
                ProgressPrint.log(f"Skipping parliamentary data for {country} due to wrong URL", "ERR!")
                if DEBUG:
                    break
                continue
            llm_response = SmartLLM.generate(
                f"llm/parliamentary_terms/{country}",
                STORED_QUERIES["EXTRACT_PARLIAMENTARY_TERMS"].replace("[HTML_CODE]", response)
            )
            if not llm_response:
                ProgressPrint.log(f"Couldn't parse parliamentary terms using LLM for {country}", "ERR!")
                if DEBUG:
                    break
                continue
            ProgressPrint.log(f"Parliamentary terms for {country} successfully retrieved", "OK")
            if DEBUG:
                break
        return country_urls

    def _fetch(self, local_cache: LocalCache, *args: Any, **kwargs: Any) -> bool:
        tl = TextLoader()
        sl = SmartLoader(tl)
        tl.simplify = True
        tl.is_in_navbox = True
        tl.html_mode = HTMLMode.OVERVIEW
        country_urls = self._pre_fetch(tl, sl)
        if country_urls is None:
            return False
        tl.is_in_navbox = False
        tl.html_mode = HTMLMode.NORMAL
        ProgressPrint.log("Fetching parliament elections in european countries")

        for country in EU_COUNTRIES_NAMES:
            if country not in country_urls:
                ProgressPrint.log(f"Skipping {country} - URL missing", "ERR!")
                if DEBUG:
                    break
                continue
            election_years_raw = SmartLLM.generate(
                f"llm/parliamentary_terms/{country}",
                STORED_QUERIES["EXTRACT_PARLIAMENTARY_TERMS"].replace(
                    "[HTML_CODE]",
                    cast(str, sl.load(URLBuilder(ENDPOINTS["WIKI"]).path_param(country_urls[country])) or "")
                )
            )
            if not election_years_raw:
                continue
            election_years = cast(Dict[str, Any], self._parse_llm(election_years_raw))
            ProgressPrint.log(f"Fetching election results for {country}", "INFO")
            for year in election_years:
                if int(year[0:4]) < START_YEAR:
                    ProgressPrint.log(f"Skipping data for year {year}")
                    continue
                ProgressPrint.log(f"Fetching parliamentary results in {int(year[0:4])}", "INFO")
                ub = URLBuilder(ENDPOINTS["WIKI"]).path_param(election_years[year])
                response = cast(str | None, sl.load(ub))
                if response is None:
                    ProgressPrint.log(f"Skipping parliamentary data for {country} in {year} due to wrong URL", "ERR!")
                    if DEBUG:
                        break
                    continue
                llm_response = SmartLLM.generate(
                    f"llm/parliamentary_results/{country}/{year}",
                    STORED_QUERIES["EXTRACT_PARTY_RESULTS"].replace("[HTML_CODE]", response),
                    LLM_RESPONSE_SCHEMAS["PARTY_RESULTS_SCHEMA"]
                )
                if not llm_response:
                    ProgressPrint.log(f"Couldn't parse election results using LLM for {country} in {year}", "ERR!")
                    if DEBUG:
                        break
                    continue
                ProgressPrint.log(f"Parliamentary data for {country} in {year} successfully retrieved", "OK")
            if DEBUG:
                break

        tl.simplify = False
        tl.html_mode = HTMLMode.DETAILED
        countries_parties: Dict[str, Dict[str, List[Tuple[str, str | None, int, bool]]]] = {}
        for country in EU_COUNTRIES_NAMES:
            if country not in country_urls:
                if DEBUG:
                    break
                continue
            election_years_raw = SmartLLM.generate(
                f"llm/parliamentary_terms/{country}",
                STORED_QUERIES["EXTRACT_PARLIAMENTARY_TERMS"].replace(
                    "[HTML_CODE]",
                    cast(str, sl.load(URLBuilder(ENDPOINTS["WIKI"]).path_param(country_urls[country])) or "")
                )
            )
            if not election_years_raw:
                continue
            election_years = cast(Dict[str, Any], self._parse_llm(election_years_raw))
            election_years_keys = list(election_years.keys())
            ProgressPrint.log(f"Collecting political parties for {country}", "INFO")
            if country not in countries_parties:
                countries_parties[country] = {}
            for year in election_years:
                if int(year[0:4]) < START_YEAR:
                    continue
                year_results_raw = SmartLLM.generate(
                    f"llm/parliamentary_results/{country}/{year}",
                    STORED_QUERIES["EXTRACT_PARTY_RESULTS"].replace(
                        "[HTML_CODE]",
                        cast(str, sl.load(URLBuilder(ENDPOINTS["WIKI"]).path_param(election_years[year])) or "")
                    ),
                    LLM_RESPONSE_SCHEMAS["PARTY_RESULTS_SCHEMA"]
                )
                if not year_results_raw:
                    continue
                year_results = cast(List[Dict[str, Any]], self._parse_llm(year_results_raw))
                for party in year_results:
                    p_url = party.get("url")
                    if p_url:
                        if countries_parties[country].get(p_url) is None:
                            countries_parties[country][p_url] = []
                        year_index = election_years_keys.index(year)
                        next_year = election_years_keys[year_index + 1] if year_index + 1 < len(election_years_keys) else None
                        countries_parties[country][p_url].append((
                            year,
                            next_year,
                            cast(int, party.get("chairs")),
                            cast(bool, party.get("inGovernment"))
                        ))
            if DEBUG:
                break

        self._countries_parties: Dict[str, Dict[str, Any]] = {}
        for country, parties in countries_parties.items():
            self._countries_parties[country] = {}
            for party_name, info_val in parties.items():
                ProgressPrint.log(f"Retrieving information about party {party_name} in {country}")
                ub = URLBuilder(ENDPOINTS["WIKI"]).path_param(party_name)
                response = cast(Optional[str], sl.load(ub))
                if response is None:
                    ProgressPrint.log(f"Skipping downloading information about party {party_name}", "ERR!")
                    if DEBUG:
                        break
                    continue
                wiki_id = self.get_wikidata_id(response)
                llm_response = SmartLLM.generate(
                    f"llm/party_info/{party_name}",
                    STORED_QUERIES["EXTRACT_PARTY_METADATA"].replace("[HTML_CODE]", response)
                )
                if not llm_response:
                    ProgressPrint.log(f"Couldn't parse party metadata using LLM for {party_name}", "ERR!")
                    if DEBUG:
                        break
                    continue
                parsed_content = self._parse_llm(llm_response)
                if isinstance(parsed_content, list):
                    # Defensive: EXTRACT_PARTY_METADATA asks for a single object, but a
                    # model can still occasionally return a list -- use the first entry
                    # rather than crashing on .get() against a list.
                    local_content = cast(Dict[str, Any], parsed_content[0]) if parsed_content else {}
                else:
                    local_content = cast(Dict[str, Any], parsed_content)
                ProgressPrint.log("Output successfully parsed", "OK")
                self._countries_parties[country][party_name] = {
                    "veVlade": info_val,
                    "wiki_id": wiki_id,
                    "plnyNazev": local_content.get("name"),
                    "zkrNazev": local_content.get("abbreviation"),
                    "barva": local_content.get("color"),
                }
            if DEBUG:
                break
        return True

    @staticmethod
    def _match_party_entity(
        wiki_id: Optional[str],
        full_name: Optional[str],
        short_name: Optional[str],
        parties_by_wiki: Dict[str, int],
        parties_by_name: Dict[str, int],
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Resolve a scraped party identity to a known entity id using the
        wiki_id → full name → abbreviation fallback chain.

        Shared by :meth:`_process` (matches against live ``politicky_subjekt``
        rows) and the evaluation harness (matches against a ground-truth-derived
        pool), so both measure the exact same resolution logic.

        Returns:
            ``(matched_id, method)`` where ``method`` is one of
            ``"wiki_id"``, ``"full_name"``, ``"abbreviation"``, or ``None``.
        """
        if wiki_id:
            match = parties_by_wiki.get(str(wiki_id))
            if match is not None:
                return match, "wiki_id"
        if full_name:
            match = parties_by_name.get(str(full_name).lower())
            if match is not None:
                return match, "full_name"
        if short_name:
            match = parties_by_name.get(str(short_name).lower())
            if match is not None:
                return match, "abbreviation"
        return None, None

    def _process(self, *args: Any, **kwargs: Any) -> None:
        if not hasattr(self, "_countries_parties"):
            return

        ve_vlade_table = cast(DBObject, DBObjects.get("ve_vlade"))

        all_parties = DB().query(
            "SELECT `id`, `wiki_id`, `nazev`, `zkratka` FROM `politicky_subjekt`"
        )
        parties_by_wiki: Dict[str, int] = {}
        parties_by_name: Dict[str, int] = {}
        if isinstance(all_parties, list):
            for p in all_parties:
                if p.get("wiki_id"):
                    parties_by_wiki[str(p["wiki_id"])] = int(p["id"])
                if p.get("nazev"):
                    parties_by_name[str(p["nazev"]).lower()] = int(p["id"])
                if p.get("zkratka"):
                    parties_by_name[str(p["zkratka"]).lower()] = int(p["id"])

        for _, parties in self._countries_parties.items():
            for party_url, info in parties.items():
                wiki_id = info.get("wiki_id")
                full_name = info.get("plnyNazev")
                short_name = info.get("zkrNazev")

                db_party_id, _method = self._match_party_entity(
                    wiki_id, full_name, short_name, parties_by_wiki, parties_by_name
                )

                if db_party_id is None:
                    ProgressPrint.log(f"Party not found {party_url}", "ERR!")
                    continue

                for (datum_od, datum_do, _chairs, in_government) in info.get("veVlade", []):
                    if not in_government:
                        continue
                    existing = DB().query(
                        "SELECT `id` FROM `ve_vlade` WHERE `ck_subjekt` = %(s)s AND `datum_od` = %(d)s LIMIT 1",
                        {"s": db_party_id, "d": datum_od}
                    )
                    if isinstance(existing, list) and existing:
                        continue
                    ve_vlade_table.save({
                        "ck_subjekt": db_party_id,
                        "datum_od": datum_od,
                        "datum_do": datum_do,
                    })

    # ── Evaluation harness (additive; never called by do()/_fetch()/_process()) ──
    # Reuses the exact production classes and cache keys so a "html" run replays
    # already-generated Gemini responses instead of issuing new API calls; only
    # the "markdown"/"raw" ablation modes hit the API (under a distinct cache key).

    @staticmethod
    def _eval_safe_llm_generate(cache_path: str, prompt: str, schema: Dict[str, Any] | None = None) -> str:
        """
        Like ``SmartLLM.generate``, but never raises.

        A single uncached record shouldn't abort an entire evaluation batch of
        20 records (e.g. an unavailable/renamed model, or a transient network
        error) — production code paths (``_fetch``/``_process``) are
        unaffected since they call ``SmartLLM.generate`` directly and this
        wrapper is only used inside the evaluation harness.

        *schema*, when given, is forwarded to ``SmartLLM.generate`` so a
        cache-miss call gets the same schema-validated-retry behaviour
        (``GeminiStrategy.generate``, ``MAX_LLM_ATTEMPTS``) as production.
        """
        try:
            return SmartLLM.generate(cache_path, prompt, schema) or ""
        except Exception as e:
            ProgressPrint.log(f"Eval: LLM call for {cache_path} failed ({e}); treating as empty response", "WARN")
            return ""

    @staticmethod
    def _eval_reduce_content(raw_html: str, content_mode: str) -> str:
        """
        Apply the requested content-reduction mode to raw HTML.

        Shared by phase 2/3 (party-results extraction) and phase 4 (party
        metadata extraction) so both consistently exercise the same ablation
        variant — production itself never reduces content before the
        phase-4 metadata call (verified: ``tl.simplify`` is left ``False``
        from the phase-2/3 loop onward), which in "html"/raw mode sends full,
        unreduced Wikipedia pages and reliably triggers real API rate-limiting
        on large party pages. ``content_mode="markdown"`` exercises the
        existing-but-otherwise-unused ``WikiLoader`` conversion instead.
        """
        if content_mode == "markdown":
            return WikiLoader(mode=HTMLExtractOutput.MARKDOWN).simplification(raw_html)
        if content_mode == "raw":
            return raw_html
        tl = TextLoader()
        tl.simplify = True
        tl.html_mode = HTMLMode.NORMAL
        return tl.simplification(raw_html)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate (chars/4); accurate enough to compare compression ratios."""
        return len(text) // 4

    def _eval_fetch_raw(self, slug: str) -> Tuple[Optional[str], Optional[int]]:
        """
        Fetch raw, unsimplified HTML for a Wikipedia page slug.

        Production's cache only ever stores *post-simplification* content for
        this endpoint, so it cannot be reused here; instead this keeps a small,
        separate on-disk cache (``cache/eval_raw_html``) so repeated evaluation
        runs don't re-hit Wikipedia.

        Returns:
            ``(html, http_status)``; ``html`` is ``None`` on failure.
        """
        if not hasattr(self, "_eval_raw_cache"):
            self._eval_raw_cache: LocalCache = LocalCache(local_path="cache/eval_raw_html")
        cached = self._eval_raw_cache.load(slug)
        if isinstance(cached, str):
            return cached, 200
        url = URLBuilder(ENDPOINTS["WIKI"]).path_param(slug).build()
        try:
            response = requests.get(url, headers={"User-Agent": "EUVoteAnalyzer/1.0"})
        except requests.RequestException:
            return None, None
        if response.status_code == 200:
            self._eval_raw_cache.store(slug, response.text)
            time.sleep(random.uniform(0.5, 1.5))
            return response.text, 200
        return None, response.status_code

    def _eval_discover_slug(self, country: str, election_year: int) -> Tuple[Optional[str], bool]:
        """
        Run the real discovery logic (:meth:`_pre_fetch` + parliamentary-terms
        extraction) to find which election page the pipeline itself would pick
        for *country*/*election_year*.

        Memoised on the instance so a batch of :meth:`eval_extract_one` calls
        only runs the (cache-hitting) navbox/term discovery once.

        Returns:
            ``(slug, parliament_page_found)``; ``slug`` is ``None`` if the
            pipeline could not find the country's parliament page or that
            specific election year.
        """
        if not hasattr(self, "_eval_country_urls"):
            tl = TextLoader()
            sl = SmartLoader(tl)
            tl.simplify = True
            tl.is_in_navbox = True
            tl.html_mode = HTMLMode.OVERVIEW
            self._eval_country_urls: Dict[str, Any] = self._pre_fetch(tl, sl) or {}
            self._eval_election_years: Dict[str, Dict[str, Any]] = {}

        parliament_page_found = country in self._eval_country_urls
        if not parliament_page_found:
            return None, False

        if country not in self._eval_election_years:
            tl = TextLoader()
            sl = SmartLoader(tl)
            tl.simplify = True
            tl.is_in_navbox = False
            tl.html_mode = HTMLMode.NORMAL
            election_years_raw = self._eval_safe_llm_generate(
                f"llm/parliamentary_terms/{country}",
                STORED_QUERIES["EXTRACT_PARLIAMENTARY_TERMS"].replace(
                    "[HTML_CODE]",
                    cast(str, sl.load(URLBuilder(ENDPOINTS["WIKI"]).path_param(self._eval_country_urls[country])) or "")
                )
            )
            self._eval_election_years[country] = cast(Dict[str, Any], self._parse_llm(election_years_raw)) if election_years_raw else {}

        return self._eval_election_years[country].get(str(election_year)), True

    def eval_extract_one(
        self,
        country: str,
        election_year: int,
        content_mode: str = "html",
        fallback_wikipedia_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the extraction pipeline for a single country/election year outside
        of ``do()``/``_fetch()``/``_process()``, producing a structured,
        per-phase evaluation record. Makes no database writes.

        Args:
            country:  Name as it appears in ``EU_COUNTRIES_NAMES``.
            election_year: Year of the election to evaluate.
            content_mode: ``"html"`` — production baseline (``TextLoader``
                NORMAL-mode cleaning, reuses the production LLM cache key so no
                new API call is made on a cache hit). ``"markdown"`` — ablation
                using the existing-but-unused ``WikiLoader`` Markdown
                conversion. ``"raw"`` — ablation sending unreduced HTML.
            fallback_wikipedia_url: Used *only* if the pipeline's own discovery
                fails to find this election year, so later phases can still be
                measured; the resulting record is flagged
                ``used_ground_truth_fallback: true`` and phase 1 is still
                reported as a discovery miss.

        Returns:
            A dict matching the ``logs/extraction_eval/<country>_<year>.json`` schema.
        """
        record: Dict[str, Any] = {"country": country, "election_year": election_year}

        discovered_slug, parliament_page_found = self._eval_discover_slug(country, election_year)
        used_fallback = discovered_slug is None and bool(fallback_wikipedia_url)
        target_slug = discovered_slug or (
            fallback_wikipedia_url.rstrip("/").split("/wiki/")[-1] if used_fallback and fallback_wikipedia_url else None
        )

        raw_html, http_status = self._eval_fetch_raw(target_slug) if target_slug else (None, None)

        record["phase_1_source_acquisition"] = {
            "parliament_page_found": parliament_page_found,
            "discovered_slug": discovered_slug,
            "matches_ground_truth": None,  # filled in by evaluate_extraction.py, which knows the ground-truth slug
            "used_ground_truth_fallback": used_fallback,
            "success": raw_html is not None,
            "page_found": target_slug is not None,
            "http_status": http_status,
        }

        if raw_html is None:
            record["phase_2_content_reduction"] = None
            record["phase_3_llm_extraction"] = None
            record["phase_4_entity_linking"] = None
            return record

        reduced = self._eval_reduce_content(raw_html, content_mode)

        raw_tokens = self._estimate_tokens(raw_html)
        reduced_tokens = self._estimate_tokens(reduced)
        record["phase_2_content_reduction"] = {
            "content_mode": content_mode,
            "raw_html_tokens_estimate": raw_tokens,
            "reduced_tokens_estimate": reduced_tokens,
            "compression_ratio": (reduced_tokens / raw_tokens) if raw_tokens else None,
        }

        cache_path = (
            f"llm/parliamentary_results/{country}/{election_year}"
            if content_mode == "html"
            else f"llm/eval_ablation_{content_mode}/{country}/{election_year}"
        )
        raw_llm_response = self._eval_safe_llm_generate(
            cache_path, STORED_QUERIES["EXTRACT_PARTY_RESULTS"].replace("[HTML_CODE]", reduced),
            LLM_RESPONSE_SCHEMAS["PARTY_RESULTS_SCHEMA"]
        )

        parsed_parties: Optional[List[Dict[str, Any]]] = None
        schema_valid = False
        if raw_llm_response:
            try:
                parsed = json.loads(raw_llm_response)
                if isinstance(parsed, list) and all(
                    isinstance(p, dict) and {"name", "chairs", "inGovernment"} <= p.keys() for p in parsed
                ):
                    schema_valid = True
                    parsed_parties = [
                        {
                            "party_name": p.get("name"),
                            "seats": p.get("chairs"),
                            "in_government": p.get("inGovernment"),
                            "url": p.get("url"),
                        }
                        for p in parsed
                    ]
            except (json.JSONDecodeError, TypeError):
                parsed_parties = None

        record["phase_3_llm_extraction"] = {
            "cache_path": cache_path,
            "raw_llm_response": raw_llm_response,
            "parsed_parties": parsed_parties,
            "schema_valid": schema_valid,
        }

        attempts: List[Dict[str, Any]] = []
        for party in (parsed_parties or []):
            p_url = party.get("url")
            wiki_id: Optional[str] = None
            full_name: Optional[str] = None
            short_name: Optional[str] = None
            raw_tok: Optional[int] = None
            reduced_tok: Optional[int] = None
            if p_url:
                party_html, _status = self._eval_fetch_raw(p_url)
                if party_html:
                    # get_wikidata_id needs the raw page (JSON-LD <script> block) --
                    # it's a plain BeautifulSoup lookup, not an LLM call, so it isn't
                    # part of the token-bloat problem content_mode addresses.
                    wiki_id = self.get_wikidata_id(party_html)
                    party_content = self._eval_reduce_content(party_html, content_mode)
                    raw_tok = self._estimate_tokens(party_html)
                    reduced_tok = self._estimate_tokens(party_content)
                    meta_raw = self._eval_safe_llm_generate(
                        f"llm/party_info/{content_mode}/{p_url}"
                        if content_mode != "html" else f"llm/party_info/{p_url}",
                        STORED_QUERIES["EXTRACT_PARTY_METADATA"].replace("[HTML_CODE]", party_content)
                    )
                    if meta_raw:
                        try:
                            parsed_meta = self._parse_llm(meta_raw)
                            if isinstance(parsed_meta, list):
                                # Defensive: the prompt now asks for a single object, but
                                # if a model still returns a list, prefer the entry whose
                                # own name/abbreviation matches the party we asked about
                                # over blindly taking the first one.
                                target = str(party.get("party_name") or "").lower()
                                meta = next(
                                    (m for m in parsed_meta if isinstance(m, dict) and (
                                        str(m.get("name") or "").lower() == target
                                        or str(m.get("abbreviation") or "").lower() == target
                                    )),
                                    (parsed_meta[0] if parsed_meta else {}),
                                )
                            else:
                                meta = parsed_meta
                            full_name = meta.get("name")
                            short_name = meta.get("abbreviation")
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            pass
            attempts.append({
                "party_name": party.get("party_name"),
                "url": p_url,
                "scraped_wiki_id": wiki_id,
                "scraped_full_name": full_name,
                "scraped_abbreviation": short_name,
                "raw_tokens_estimate": raw_tok,
                "reduced_tokens_estimate": reduced_tok,
            })

        record["phase_4_entity_linking"] = {"attempts": attempts}
        return record