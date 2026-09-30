<?php
namespace API;
require_once __DIR__ . '/db.php';

new Endpoint("terms", function(){
    $dataDir = __DIR__ . '/../data';
    $terms = [];
    if (is_dir($dataDir)) {
        foreach (glob($dataDir . '/term_*', GLOB_ONLYDIR) as $dir) {
            $terms[] = basename($dir);
        }
        usort($terms, function ($a, $b) {
            $a = str_replace('term_', '', $a);
            $b = str_replace('term_', '', $b);
            if($a == $b){
                return 0;
            }
            return ($a < $b) ? -1 : 1;
        });
    }
    $db = DB::getInstance();
    try{
        $result = $db->select("SELECT * FROM `zastupitelstvo` ORDER BY `funkcni_obdobi_od`");
    }
    catch(\Exception $e){
        return Response::error($e->getMessage());
    }
    $termArray = [];
    $order = 0;
    foreach($result as $row){
        if(in_array("term_".$row["poradi"], $terms)){
            $termArray["term_".$row["poradi"]] = ["order" => $order, "start" => $row["funkcni_obdobi_od"], "end" => $row["funkcni_obdobi_do"]];
        }
        $order++;
    }
    return Response::ok($termArray);
});

new Endpoint("compare", function($data) {
    $term = intval($data["term"]);
    $mep1 = intval($data["mep1"]);
    $mep2 = intval($data["mep2"]);

    if ($term <= 0 || $mep1 <= 0 || $mep2 <= 0) {
        return Response::error('Missing or invalid parameters (term, mep1, mep2)');
    }

    $db = DB::getInstance();

    try {
        $sql = "
            SELECT m1.ck_moznost AS moznost1,
                   m2.ck_moznost AS moznost2,
                   h.hlasovani_kategorie AS kat
            FROM hlasovani_clena m1
            JOIN hlasovani_clena m2 ON  m2.ck_hlasovani = m1.ck_hlasovani
                                     AND m2.ck_clen      = :mep2
                                     AND m2.ck_moznost   IN (1, 2, 3)
            JOIN hlasovani h ON h.id = m1.ck_hlasovani
            JOIN zasedani  z ON z.id = h.ck_zasedani
            WHERE m1.ck_clen     = :mep1
              AND m1.ck_moznost  IN (1, 2, 3)
              AND z.ck_zastupitelstvo = :term
              AND h.validni      IS NOT FALSE
              AND h.proceduralni IS NOT TRUE
        ";

        $rows = $db->select($sql, [':mep1' => $mep1, ':mep2' => $mep2, ':term' => $term]);

        $total = 0;
        $agree_total = 0;
        $by_kat = [];

        foreach ($rows as $r) {
            $total++;
            $matched = ($r['moznost1'] === $r['moznost2']) ? 1 : 0;
            $agree_total += $matched;
            
            $kat = $r['kat'] ?? null;
            if ($kat === null || $kat === '') continue;
            
            if (!isset($by_kat[$kat])) {
                $by_kat[$kat] = ['agree' => 0, 'total' => 0];
            }
            $by_kat[$kat]['agree'] += $matched;
            $by_kat[$kat]['total']++;
        }

        $categories = [];
        foreach ($by_kat as $kat => $d) {
            $categories[$kat] = [
                'agree' => $d['agree'],
                'total' => $d['total'],
                'pct'   => $d['total'] > 0 ? round($d['agree'] / $d['total'] * 100, 1) : null,
            ];
        }

        uasort($categories, function ($a, $b) {
            return ($b['pct'] ?? -1) <=> ($a['pct'] ?? -1);
        });

        $getMepInfo = function($id) use ($db, $term) {
            $sqlInfo = "
                SELECT c.jmeno, c.prijmeni, c.obcanstvi,
                       ep.zkratka  AS ep_zkratka,  ep.barva  AS ep_barva,
                       nat.zkratka AS nat_zkratka, nat.barva AS nat_barva
                FROM clen c
                LEFT JOIN (
                    SELECT pk.ck_clen, pk.ck_subjekt,
                           ROW_NUMBER() OVER (
                               PARTITION BY pk.ck_clen ORDER BY pk.datum_od DESC
                           ) AS rn
                    FROM prislusi_k pk
                    JOIN politicky_subjekt ps2 ON ps2.id = pk.ck_subjekt
                                               AND ps2.typ = 'POLITICAL_GROUP'
                    JOIN zastupitelstvo zt ON zt.poradi = :term_ep
                    WHERE pk.datum_od <= IFNULL(zt.funkcni_obdobi_do,
                                                DATE_ADD(zt.funkcni_obdobi_od, INTERVAL 5 YEAR))
                      AND IFNULL(pk.datum_do, '9999-12-31') >= zt.funkcni_obdobi_od
                      AND pk.ck_clen = :id_ep
                ) best_ep ON best_ep.ck_clen = c.id AND best_ep.rn = 1
                LEFT JOIN politicky_subjekt ep ON ep.id = best_ep.ck_subjekt
                LEFT JOIN (
                    SELECT pk.ck_clen, pk.ck_subjekt,
                           ROW_NUMBER() OVER (
                               PARTITION BY pk.ck_clen ORDER BY pk.datum_od DESC
                           ) AS rn
                    FROM prislusi_k pk
                    JOIN politicky_subjekt ps2 ON ps2.id = pk.ck_subjekt
                                               AND ps2.typ = 'NATIONAL_POLITICAL_GROUP'
                    JOIN zastupitelstvo zt ON zt.poradi = :term_nat
                    WHERE pk.datum_od <= IFNULL(zt.funkcni_obdobi_do,
                                                DATE_ADD(zt.funkcni_obdobi_od, INTERVAL 5 YEAR))
                      AND IFNULL(pk.datum_do, '9999-12-31') >= zt.funkcni_obdobi_od
                      AND pk.ck_clen = :id_nat
                ) best_nat ON best_nat.ck_clen = c.id AND best_nat.rn = 1
                LEFT JOIN politicky_subjekt nat ON nat.id = best_nat.ck_subjekt
                WHERE c.id = :id_main
                LIMIT 1
            ";

            $res = $db->select($sqlInfo, [
                ':term_ep'  => $term,
                ':id_ep'    => $id,
                ':term_nat' => $term,
                ':id_nat'   => $id,
                ':id_main'  => $id,
            ]);

            $r       = $res[0] ?? [];
            $epBarva  = $r['ep_barva']  ?? null;
            $natBarva = $r['nat_barva'] ?? null;
            return [
                'id'        => $id,
                'jmeno'     => $r['jmeno']       ?? '',
                'prijmeni'  => $r['prijmeni']    ?? '',
                'obcanstvi' => $r['obcanstvi']   ?? '',
                'zkratka'   => $r['ep_zkratka']  ?? '',
                'barva'     => $epBarva,
                'color'     => $epBarva  ? '#' . $epBarva  : '#9E9E9E',
                'nat_party' => $r['nat_zkratka'] ?? '',
                'nat_barva' => $natBarva,
                'nat_color' => $natBarva ? '#' . $natBarva : '#9E9E9E',
            ];
        };

        return Response::ok([
            'mep1'       => $getMepInfo($mep1),
            'mep2'       => $getMepInfo($mep2),
            'overall'    => [
                'agree' => $agree_total,
                'total' => $total,
                'pct'   => $total > 0 ? round($agree_total / $total * 100, 1) : null,
            ],
            'categories' => $categories,
        ]);

    } catch (\Exception $e) {
        return Response::error($e->getMessage());
    }
}, ["term", "mep1", "mep2"]);

new Endpoint("member_stats", function($data) {
    $term = intval($data['term'] ?? 0);
    if ($term <= 0) {
        return Response::error('Missing or invalid term');
    }

    $db = DB::getInstance();

    try {
        $sql = "
            SELECT
                agg.ck_clen AS id,
                c.jmeno, c.prijmeni, c.obcanstvi,
                ps.zkratka, ps.barva,
                agg.za, agg.proti, agg.zdrzeni,
                (agg.za + agg.proti + agg.zdrzeni) AS celkem
            FROM (
                SELECT
                    sc.ck_clen,
                    SUM(CASE WHEN sc.ck_moznost = 1 THEN sc.sum ELSE 0 END) AS za,
                    SUM(CASE WHEN sc.ck_moznost = 2 THEN sc.sum ELSE 0 END) AS proti,
                    SUM(CASE WHEN sc.ck_moznost = 3 THEN sc.sum ELSE 0 END) AS zdrzeni
                FROM statistika_clen sc
                WHERE sc.ck_zastupitelstvo = :term
                  AND sc.ck_moznost IN (1, 2, 3)
                GROUP BY sc.ck_clen
            ) agg
            JOIN clen c ON c.id = agg.ck_clen
            LEFT JOIN (
                SELECT ck_clen, ck_subjekt
                FROM (
                    SELECT ck_clen, ck_subjekt,
                           ROW_NUMBER() OVER (
                               PARTITION BY ck_clen
                               ORDER BY is_pg DESC, n DESC
                           ) AS rn
                    FROM (
                        SELECT sc2.ck_clen, sc2.ck_subjekt,
                               IFNULL(ps2.typ = 'POLITICAL_GROUP', 0) AS is_pg,
                               SUM(sc2.sum) AS n
                        FROM statistika_clen sc2
                        LEFT JOIN politicky_subjekt ps2 ON ps2.id = sc2.ck_subjekt
                        WHERE sc2.ck_zastupitelstvo = :term2
                          AND sc2.ck_moznost IN (1, 2, 3)
                        GROUP BY sc2.ck_clen, sc2.ck_subjekt, ps2.typ
                    ) agg2
                ) ranked
                WHERE rn = 1
            ) best ON best.ck_clen = agg.ck_clen
            LEFT JOIN politicky_subjekt ps ON ps.id = best.ck_subjekt
            ORDER BY c.prijmeni, c.jmeno
        ";

        $rows = $db->select($sql, [':term' => $term, ':term2' => $term]);
        $rows = array_map(function($r) {
            $r['color'] = ($r['barva'] ?? null) ? '#' . $r['barva'] : '#9E9E9E';
            return $r;
        }, $rows);
        return Response::ok(array_values($rows));

    } catch (\Exception $e) {
        return Response::error($e->getMessage());
    }
}, ["term"]);

new Endpoint("subject_stats", function($data) {
    $term = intval($data['term'] ?? 0);
    if ($term <= 0) {
        return Response::error('Missing or invalid term');
    }

    $db = DB::getInstance();

    try {
        $sql = "
            SELECT
                ss.ck_subjekt AS id,
                ps.zkratka, ps.barva, ps.typ,
                SUM(CASE WHEN ss.ck_moznost = 1 THEN ss.sum ELSE 0 END) AS za,
                SUM(CASE WHEN ss.ck_moznost = 2 THEN ss.sum ELSE 0 END) AS proti,
                SUM(CASE WHEN ss.ck_moznost = 3 THEN ss.sum ELSE 0 END) AS zdrzeni,
                SUM(CASE WHEN ss.ck_moznost IN (1,2,3) THEN ss.sum ELSE 0 END) AS celkem
            FROM statistika_subjekt ss
            JOIN politicky_subjekt ps ON ps.id = ss.ck_subjekt
            WHERE ss.ck_zastupitelstvo = :term
              AND ss.ck_moznost IN (1, 2, 3)
            GROUP BY ss.ck_subjekt, ps.zkratka, ps.barva, ps.typ
            ORDER BY celkem DESC
        ";

        $rows = $db->select($sql, [':term' => $term]);
        $rows = array_map(function($r) {
            $r['color'] = ($r['barva'] ?? null) ? '#' . $r['barva'] : '#9E9E9E';
            return $r;
        }, $rows);
        return Response::ok(array_values($rows));

    } catch (\Exception $e) {
        return Response::error($e->getMessage());
    }
}, ["term"]);

new Endpoint("mep_list", function($data) {
    $term = intval($data['term'] ?? 0);
    if ($term <= 0) {
        return Response::error('Missing or invalid term');
    }

    $db = DB::getInstance();

    try {
        $sql = "
            SELECT best.ck_clen AS id,
                   c.jmeno, c.prijmeni, c.obcanstvi,
                   ps.zkratka, ps.barva
            FROM (
                SELECT ck_clen, ck_subjekt
                FROM (
                    SELECT ck_clen, ck_subjekt,
                           ROW_NUMBER() OVER (
                               PARTITION BY ck_clen
                               ORDER BY is_pg DESC, n DESC
                           ) AS rn
                    FROM (
                        SELECT sc.ck_clen, sc.ck_subjekt,
                               IFNULL(ps2.typ = 'POLITICAL_GROUP', 0) AS is_pg,
                               SUM(sc.sum) AS n
                        FROM statistika_clen sc
                        LEFT JOIN politicky_subjekt ps2 ON ps2.id = sc.ck_subjekt
                        WHERE sc.ck_zastupitelstvo = :term
                          AND sc.ck_moznost IN (1, 2, 3)
                        GROUP BY sc.ck_clen, sc.ck_subjekt, ps2.typ
                    ) agg
                ) ranked
                WHERE rn = 1
            ) best
            JOIN clen c ON c.id = best.ck_clen
            LEFT JOIN politicky_subjekt ps ON ps.id = best.ck_subjekt
            ORDER BY c.prijmeni, c.jmeno
        ";

        $rows = $db->select($sql, [':term' => $term]);
        return Response::ok(array_values($rows));

    } catch (\Exception $e) {
        return Response::error($e->getMessage());
    }
}, ["term"]);