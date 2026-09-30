"""
Integration test harness for the EUVoteAnalyzer pipeline.

Tester seeds an isolated test database (port 3331) with known fixture data,
runs the Preprocessor, and then compares each Analyser output against stored
ground-truth JSON files.  Results are printed using ProgressPrint so they
integrate with the standard pipeline logging.

Usage (via CLI):
    python main.py --test          # seed + run
    python main.py --test-init     # seed only
    python main.py --test-run      # run against existing seed
"""

import json
import math
import os
from typing import Any, Dict, List, Tuple

from EUVoteAnalyzer.core.utils import ProgressPrint
from EUVoteAnalyzer.database.orm import DB
from EUVoteAnalyzer.logic.preprocess import Preprocessor
from EUVoteAnalyzer.logic.analyser import Analyser

class Tester:
    """
    Seeds a test database, runs the preprocessing pipeline, and verifies analyser output.

    ``TEST_TERM`` (99) is a synthetic parliamentary term used exclusively in the test
    schema so test data never collides with real data.  All fixture JSON files live in
    ``test/data/`` and ground-truth expectations in ``test/ground_truth/``.
    """

    TEST_TERM = 99
    _DATA = os.path.join(os.path.dirname(__file__), "data")
    _GT = os.path.join(os.path.dirname(__file__), "ground_truth")
    _FLOAT_TOL = 1e-2  # 0.01 absolute tolerance for rounded DB values

    def _load(self, filename: str) -> List[Dict[str, Any]]:
        """Load a fixture JSON file from the ``test/data/`` directory."""
        with open(os.path.join(self._DATA, filename), encoding="utf-8") as f:
            return json.load(f)
        
    def _approx_eq(self, a: Any, b: Any) -> bool:
        """Return ``True`` if *a* and *b* are equal, with floating-point tolerance for numeric types."""
        if isinstance(a, float) and isinstance(b, (float, int)):
            return math.isclose(float(a), float(b), abs_tol=self._FLOAT_TOL, rel_tol=1e-6)
        if isinstance(b, float) and isinstance(a, (float, int)):
            return math.isclose(float(a), float(b), abs_tol=self._FLOAT_TOL, rel_tol=1e-6)
        return a == b
    
    def _dict_errors(self, actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
        """
        Compare *actual* against every key in *expected* and return a list of mismatch descriptions.

        Args:
            actual:   The dict produced by the analyser under test.
            expected: The ground-truth dict loaded from ``ground_truth/``.

        Returns:
            A list of human-readable error strings; empty if all values match.
        """
        errs : List[str]= []
        for key, exp in expected.items():
            if key not in actual:
                errs.append(f"missing key '{key}'")
            elif not self._approx_eq(actual[key], exp):
                errs.append(f"key '{key}': got {actual[key]!r}, expected {exp!r}")
        return errs
    
    def _compare(self, name: str, actual: List[Dict[str, Any]], expected: List[Dict[str, Any]], id_keys: Tuple[str, ...]) -> bool:
        """
        Verify that *actual* matches *expected* by matching rows on *id_keys*.

        Args:
            name:     Test name used in log messages.
            actual:   Rows returned by the analyser.
            expected: Ground-truth rows loaded from file.
            id_keys:  Column names used to identify and match corresponding rows.

        Returns:
            ``True`` if all rows match within tolerance, ``False`` otherwise.
        """
        if len(actual) != len(expected):
            ProgressPrint.log(
                f"  {name}: FAIL – row count {len(actual)} != {len(expected)}", "ERR!"
            )
            return False

        # Match rows by ID keys; fall back to positional if IDs don't uniquely match.
        def id_of(row: Dict[str, Any]) -> tuple[Any, ...]:
            return tuple(row.get(k) for k in id_keys)

        exp_by_id = {id_of(r): r for r in expected}
        act_by_id = {id_of(r): r for r in actual}

        all_ok = True
        for eid, exp_row in exp_by_id.items():
            if eid not in act_by_id:
                ProgressPrint.log(f"  {name}: FAIL – expected row with {dict(zip(id_keys, eid))} not found", "ERR!")
                all_ok = False
                continue
            errs = self._dict_errors(act_by_id[eid], exp_row)
            if errs:
                for e in errs:
                    ProgressPrint.log(f"  {name} {dict(zip(id_keys, eid))}: {e}", "ERR!")
                all_ok = False

        return all_ok
    
    def _load_gt(self, filename: str) -> Any:
        """Load a ground-truth JSON file from the ``test/ground_truth/`` directory."""
        with open(os.path.join(self._GT, filename), encoding="utf-8") as f:
            return json.load(f)

    def init(self) -> None:
        """
        Seed the test database with fixture data and run the Preprocessor.

        Inserts reference data (vote options, parties, MEPs, sessions, votes) from
        JSON fixtures under ``test/data/``, cleans stale precomputed rows for
        ``TEST_TERM``, and then calls ``Preprocessor().do(term=TEST_TERM)`` so
        the derived statistics tables are populated before :meth:`run` is called.
        """
        ProgressPrint.log("Seeding test data…", "INFO")
        db = DB()

        ProgressPrint.log("  hlasovaci_moznost", "INFO")
        for row in self._load("hlasovaci_moznost.json"):
            db.query(
                "INSERT INTO `hlasovaci_moznost` (`id`, `text`) VALUES (%(id)s, %(text)s)"
                " ON DUPLICATE KEY UPDATE `text` = VALUES(`text`)",
                row,
            )

        ProgressPrint.log("  zastupitelstvo", "INFO")
        for row in self._load("zastupitelstvo.json"):
            db.query(
                "INSERT INTO `zastupitelstvo` (`poradi`, `funkcni_obdobi_od`, `funkcni_obdobi_do`)"
                " VALUES (%(poradi)s, %(funkcni_obdobi_od)s, %(funkcni_obdobi_do)s)"
                " ON DUPLICATE KEY UPDATE `funkcni_obdobi_od` = VALUES(`funkcni_obdobi_od`)",
                row,
            )

        ProgressPrint.log("  politicky_subjekt", "INFO")
        for row in self._load("politicky_subjekt.json"):
            db.query(
                "INSERT INTO `politicky_subjekt` (`id`, `zkratka`, `barva`, `typ`)"
                " VALUES (%(id)s, %(zkratka)s, %(barva)s, %(typ)s)"
                " ON DUPLICATE KEY UPDATE `zkratka` = VALUES(`zkratka`)",
                row,
            )

        ProgressPrint.log("  clen", "INFO")
        for row in self._load("clen.json"):
            db.query(
                "INSERT INTO `clen` (`id`, `jmeno`, `prijmeni`, `obcanstvi`)"
                " VALUES (%(id)s, %(jmeno)s, %(prijmeni)s, %(obcanstvi)s)"
                " ON DUPLICATE KEY UPDATE `jmeno` = VALUES(`jmeno`)",
                row,
            )

        ProgressPrint.log("  prislusi_k", "INFO")
        db.query("DELETE FROM `prislusi_k` WHERE `ck_zastupitelstvo` = %(TEST_TERM)s", {"TEST_TERM" : self.TEST_TERM})
        for row in self._load("prislusi_k.json"):
            db.query(
                "INSERT INTO `prislusi_k` (`ck_clen`, `ck_zastupitelstvo`, `ck_subjekt`, `datum_od`)"
                " VALUES (%(ck_clen)s, %(ck_zastupitelstvo)s, %(ck_subjekt)s, %(datum_od)s)",
                row,
            )

        ProgressPrint.log("  zasedani", "INFO")
        for row in self._load("zasedani.json"):
            db.query(
                "INSERT INTO `zasedani` (`id`, `ck_zastupitelstvo`, `datum_od`)"
                " VALUES (%(id)s, %(ck_zastupitelstvo)s, %(datum_od)s)"
                " ON DUPLICATE KEY UPDATE `datum_od` = VALUES(`datum_od`)",
                row,
            )

        ProgressPrint.log("  hlasovani", "INFO")
        for row in self._load("hlasovani.json"):
            db.query(
                "INSERT INTO `hlasovani` (`id`, `ck_zasedani`, `predmet`, `cas`, `validni`, `proceduralni`)"
                " VALUES (%(id)s, %(ck_zasedani)s, %(predmet)s, %(cas)s, %(validni)s, %(proceduralni)s)"
                " ON DUPLICATE KEY UPDATE `predmet` = VALUES(`predmet`)",
                row,
            )

        ProgressPrint.log("  hlasovani_clena", "INFO")
        db.query(
            "DELETE hc FROM `hlasovani_clena` hc"
            " JOIN `hlasovani` h ON h.id = hc.ck_hlasovani"
            " JOIN `zasedani` z ON z.id = h.ck_zasedani"
            " WHERE z.ck_zastupitelstvo = %(TEST_TERM)s",
            {"TEST_TERM" : self.TEST_TERM},
        )
        for row in self._load("hlasovani_clena.json"):
            db.query(
                "INSERT INTO `hlasovani_clena` (`ck_clen`, `ck_hlasovani`, `ck_moznost`)"
                " VALUES (%(ck_clen)s, %(ck_hlasovani)s, %(ck_moznost)s)",
                row,
            )

        # Clean stale precomputed data for this term before recomputing
        db.query(
            "DELETE ssz FROM `soudrznost_subjektu_zasedani` ssz"
            " JOIN `zasedani` z ON z.id = ssz.ck_zasedani"
            " WHERE z.ck_zastupitelstvo = %(TEST_TERM)s",
            {"TEST_TERM" : self.TEST_TERM},
        )
        db.query(
            "DELETE bp FROM `byl_pritomen` bp"
            " JOIN `zasedani` z ON z.id = bp.ck_zasedani"
            " WHERE z.ck_zastupitelstvo = %(TEST_TERM)s",
            {"TEST_TERM" : self.TEST_TERM},
        )

        ProgressPrint.log("Running Preprocessor for test term…", "INFO")
        Preprocessor().do(term=self.TEST_TERM)
        ProgressPrint.log("Test data seeded.", "OK")

    def _test_party_cohesion(self, analyser: Analyser) -> bool:
        """Run the party_cohesion analyser and compare against ground truth."""
        ProgressPrint.log("party_cohesion…", "INFO")
        actual = analyser.party_cohesion(self.TEST_TERM)
        expected = self._load_gt("party_cohesion.json")
        ok = self._compare("party_cohesion", actual, expected, ("id",))
        ProgressPrint.log(f"  party_cohesion: {'PASS' if ok else 'FAIL'}", "OK" if ok else "ERR!")
        return ok

    def _test_country_cohesion(self, analyser: Analyser) -> bool:
        """Run the country_cohesion analyser and compare against ground truth."""
        ProgressPrint.log("country_cohesion…", "INFO")
        actual = analyser.country_cohesion(self.TEST_TERM)
        expected = self._load_gt("country_cohesion.json")
        ok = self._compare("country_cohesion", actual, expected, ("country",))
        ProgressPrint.log(f"  country_cohesion: {'PASS' if ok else 'FAIL'}", "OK" if ok else "ERR!")
        return ok

    def _test_inter_faction_cohesion(self, analyser: Analyser) -> bool:
        """Run the inter_faction_cohesion analyser and compare against ground truth."""
        ProgressPrint.log("inter_faction_cohesion…", "INFO")
        actual = analyser.inter_faction_cohesion(self.TEST_TERM)
        expected = self._load_gt("inter_faction_cohesion.json")
        ok = self._compare("inter_faction_cohesion", actual, expected, ("subj1_id", "subj2_id"))
        ProgressPrint.log(f"  inter_faction_cohesion: {'PASS' if ok else 'FAIL'}", "OK" if ok else "ERR!")
        return ok

    def _test_mep_participation(self, analyser: Analyser) -> bool:
        """Run the mep_participation analyser and compare against ground truth."""
        ProgressPrint.log("mep_participation…", "INFO")
        actual = analyser.mep_participation(self.TEST_TERM)
        expected = self._load_gt("mep_participation.json")
        ok = self._compare("mep_participation", actual, expected, ("ck_clen",))
        ProgressPrint.log(f"  mep_participation: {'PASS' if ok else 'FAIL'}", "OK" if ok else "ERR!")
        return ok

    def _test_mep_loyalty(self, analyser: Analyser) -> bool:
        """Run the mep_loyalty analyser and compare against ground truth."""
        ProgressPrint.log("mep_loyalty…", "INFO")
        actual = analyser.mep_loyalty(self.TEST_TERM)
        expected = self._load_gt("mep_loyalty.json")
        ok = self._compare("mep_loyalty", actual, expected, ("ck_clen", "ck_subjekt"))
        ProgressPrint.log(f"  mep_loyalty: {'PASS' if ok else 'FAIL'}", "OK" if ok else "ERR!")
        return ok

    def run(self) -> bool:
        """
        Execute all test cases against the seeded database and report results.

        Returns:
            ``True`` if every test case passes, ``False`` if any fail.
        """
        ProgressPrint.log(f"=== Running test suite (term {self.TEST_TERM}) ===", "INFO")
        analyser = Analyser()

        tests = [
            self._test_party_cohesion,
            self._test_country_cohesion,
            self._test_inter_faction_cohesion,
            self._test_mep_participation,
            self._test_mep_loyalty,
        ]

        passed = sum(1 for t in tests if t(analyser))
        failed = len(tests) - passed

        summary_type = "OK" if failed == 0 else "ERR!"
        ProgressPrint.log(
            f"=== Results: {passed}/{len(tests)} passed, {failed} failed ===",
            summary_type,
        )
        return failed == 0
