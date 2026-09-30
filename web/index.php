<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title data-text="title"></title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.datatables.net/1.13.7/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    <link href="assets/css/index.css" rel="stylesheet">
</head>
<body>

<div id="loading-overlay">
    <div class="spinner-border text-primary" role="status"></div>
    <span data-text="loader"></span>
</div>

<div class="header-bar d-flex align-items-center justify-content-between flex-wrap gap-2">
    <div class="d-flex align-items-center gap-2">
        <img src="assets/img/logo.png" alt="EUVoteAnalyzer logo" class="header-logo">
        <h1 class="mb-0" data-text="title"></h1>
    </div>
    <div class="d-flex align-items-center gap-3">
        <a href="methodology" class="btn btn-sm btn-outline-light" data-text="methodologyLink"></a>
        <label class="text-white mb-0 fw-semibold small" data-text="parliamentaryTerm"></label>
        <select id="term-select" class="form-select form-select-sm" style="width: auto; min-width: 160px"></select>
        <select id="language-select" class="form-select form-select-sm" style="width: auto; max-width: 100px"></select>
    </div>
</div>

<div class="container-fluid py-4">
    <ul class="nav nav-tabs mb-4" id="mainTabs" role="tablist">
        <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tab-party"     type="button" data-text="tabs.factionCohesion.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-country"   type="button" data-text="tabs.countryCohesion.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-inter"     type="button" data-text="tabs.interFactionAgreement.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-loyalty"   type="button" data-text="tabs.mepLoyalty.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-gov-loyalty" type="button" data-text="tabs.govLoyalty.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-part"      type="button" data-text="tabs.mepParticipation.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-corr"      type="button" data-text="tabs.categoryCorrelations.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-heatmap"   type="button" data-text="tabs.topicProfiles.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-deviation" type="button" data-text="tabs.deviationRules.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-opposition"type="button" data-text="tabs.oppositionRules.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-compare"   type="button" data-text="tabs.mepComparison.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-mstats"   type="button" data-text="tabs.memberStats.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-sstats"   type="button" data-text="tabs.subjectStats.name"></button></li>
        <li class="nav-item"><button class="nav-link"        data-bs-toggle="tab" data-bs-target="#tab-anomaly" type="button" data-text="tabs.anomalyDetection.name"></button></li>
    </ul>

    <div class="tab-content">

        <!-- ── Party Cohesion ── -->
        <div class="tab-pane fade show active" id="tab-party">
            <div class="card">
                <div class="card-header d-flex align-items-center gap-2 py-3">
                    <strong data-text="tabs.factionCohesion.title"></strong>
                    <span class="stat-badge ms-auto" id="party-stat"></span>
                    <span class="text-muted small"><span data-text="tabs.factionCohesion.titleDescription"></span></span>
                </div>
                <div class="card-body p-4">
                    <div class="chart-wrap" style="height: 360px"><canvas id="chart-party"></canvas></div>
                </div>
            </div>
        </div>

        <!-- ── Country Cohesion ── -->
        <div class="tab-pane fade" id="tab-country">
            <div class="card">
                <div class="card-header d-flex align-items-center gap-2 py-3">
                    <strong data-text="tabs.countryCohesion.title"></strong>
                    <span class="stat-badge ms-auto" id="country-stat"></span>
                    <span class="text-muted small"><span data-text="tabs.countryCohesion.titleDescription"></span></span>
                </div>
                <div class="card-body p-4">
                    <div class="chart-wrap" style="height: 640px"><canvas id="chart-country"></canvas></div>
                </div>
            </div>
        </div>

        <!-- ── Inter-faction ── -->
        <div class="tab-pane fade" id="tab-inter">
            <div class="card">
                <div class="card-header py-3">
                    <strong data-text="tabs.interFactionAgreement.title"></strong>
                    <span class="text-muted small ms-2" data-text="tabs.interFactionAgreement.subtitle"></span>
                </div>
                <div class="card-body p-4 overflow-auto">
                    <div id="heatmap-container"></div>
                    <div class="mt-3 small d-flex align-items-center gap-2">
                        <span class="fw-semibold text-muted"><span data-text="tabs.interFactionAgreement.scale"></span></span>
                        <span style="background:hsl(0,70%,45%);color:white;padding:2px 10px;border-radius:4px"><span data-text="tabs.interFactionAgreement.low"></span></span>
                        <span style="background:hsl(30,80%,48%);color:white;padding:2px 10px;border-radius:4px">&nbsp;</span>
                        <span style="background:hsl(60,80%,42%);color:white;padding:2px 10px;border-radius:4px"><span data-text="tabs.interFactionAgreement.medium"></span></span>
                        <span style="background:hsl(90,70%,38%);color:white;padding:2px 10px;border-radius:4px">&nbsp;</span>
                        <span style="background:hsl(120,65%,35%);color:white;padding:2px 10px;border-radius:4px"><span data-text="tabs.interFactionAgreement.high"></span></span>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── MEP Loyalty ── -->
        <div class="tab-pane fade" id="tab-loyalty">
            <div class="row g-4">
                <div class="col-lg-4">
                    <div class="card h-100">
                        <div class="card-header py-3"><strong data-text="tabs.mepLoyalty.graphTitle"></strong></div>
                        <div class="card-body p-4">
                            <div class="chart-wrap" style="height: 280px"><canvas id="chart-loyalty-dist"></canvas></div>
                        </div>
                    </div>
                </div>
                <div class="col-lg-8">
                    <div class="card">
                        <div class="card-header py-3 d-flex align-items-center gap-2">
                            <strong data-text="tabs.mepLoyalty.tableTitle"></strong>
                            <span class="stat-badge ms-auto" id="loyalty-stat"></span>
                        </div>
                        <div class="card-body p-3">
                            <table id="table-loyalty" class="table table-sm table-striped table-hover" style="width:100%">
                                <thead>
                                    <tr>
                                        <th data-text="tabs.mepLoyalty.table.name"></th>
                                        <th data-text="tabs.mepLoyalty.table.country"></th>
                                        <th data-text="tabs.mepLoyalty.table.party"></th>
                                        <th data-text="tabs.mepLoyalty.table.totalVotes"></th>
                                        <th data-text="tabs.mepLoyalty.table.loyalVotes"></th>
                                        <th data-text="tabs.mepLoyalty.table.loyaltyScore"></th>
                                    </tr>
                                </thead>
                                <tbody></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Government/Opposition Loyalty ── -->
        <div class="tab-pane fade" id="tab-gov-loyalty">
            <!--div class="alert alert-warning small mb-3" data-text="tabs.govLoyalty.coverageNote"></div-->
            <div class="row g-4">
                <div class="col-lg-5">
                    <div class="card h-100">
                        <div class="card-header py-3"><strong data-text="tabs.govLoyalty.graphTitle"></strong></div>
                        <div class="card-body p-4">
                            <div class="chart-wrap" style="height: 360px"><canvas id="chart-gov-loyalty"></canvas></div>
                        </div>
                    </div>
                </div>
                <div class="col-lg-7">
                    <div class="card">
                        <div class="card-header py-3 d-flex align-items-center gap-2 flex-wrap">
                            <strong data-text="tabs.govLoyalty.tableTitle"></strong>
                            <select id="gov-loyalty-status-filter" class="form-select form-select-sm ms-auto" style="width: auto">
                                <option value="all" data-text="tabs.govLoyalty.filterAll"></option>
                                <option value="government" data-text="tabs.govLoyalty.government"></option>
                                <option value="opposition" data-text="tabs.govLoyalty.opposition"></option>
                            </select>
                            <span class="stat-badge" id="gov-loyalty-stat"></span>
                        </div>
                        <div class="card-body p-3">
                            <p class="text-muted small mb-2" data-text="tabs.govLoyalty.explainer"></p>
                            <table id="table-gov-loyalty" class="table table-sm table-striped table-hover" style="width:100%">
                                <thead>
                                    <tr>
                                        <th data-text="tabs.govLoyalty.table.name"></th>
                                        <th data-text="tabs.govLoyalty.table.country"></th>
                                        <th data-text="tabs.govLoyalty.table.epGroup"></th>
                                        <th data-text="tabs.govLoyalty.table.nationalParty"></th>
                                        <th data-text="tabs.govLoyalty.table.status"></th>
                                        <th data-text="tabs.govLoyalty.table.totalVotes"></th>
                                        <th data-text="tabs.govLoyalty.table.loyalVotes"></th>
                                        <th data-text="tabs.govLoyalty.table.loyaltyScore"></th>
                                    </tr>
                                </thead>
                                <tbody></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── MEP Participation ── -->
        <div class="tab-pane fade" id="tab-part">
            <div class="row g-4">
                <div class="col-lg-4">
                    <div class="card h-100">
                        <div class="card-header py-3"><strong data-text="tabs.mepParticipation.graphTitle"></strong></div>
                        <div class="card-body p-4">
                            <div class="chart-wrap" style="height: 280px"><canvas id="chart-part-dist"></canvas></div>
                        </div>
                    </div>
                </div>
                <div class="col-lg-8">
                    <div class="card">
                        <div class="card-header py-3 d-flex align-items-center gap-2">
                            <strong data-text="tabs.mepParticipation.tableTitle"></strong>
                            <span class="stat-badge ms-auto" id="part-stat"></span>
                        </div>
                        <div class="card-body p-3">
                            <table id="table-participation" class="table table-sm table-striped table-hover" style="width:100%">
                                <thead>
                                    <tr>
                                        <th data-text="tabs.mepParticipation.table.name"></th>
                                        <th data-text="tabs.mepParticipation.table.country"></th>
                                        <th data-text="tabs.mepParticipation.table.present"></th>
                                        <th data-text="tabs.mepParticipation.table.totalSessions"></th>
                                        <th data-text="tabs.mepParticipation.table.participationScore"></th>
                                    </tr>
                                </thead>
                                <tbody></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Category Correlations ── -->
        <div class="tab-pane fade" id="tab-corr">
            <div class="card">
                <div class="card-header py-3">
                    <div class="d-flex flex-wrap align-items-center gap-3">
                        <strong data-text="tabs.categoryCorrelations.title"></strong>
                        <div class="d-flex align-items-center gap-2 ms-auto flex-wrap">
                            <label class="small fw-semibold text-muted mb-0" data-text="tabs.categoryCorrelations.policyArea"></label>
                            <select id="corr-category" class="form-select form-select-sm" style="min-width:260px"></select>
                            <label class="small fw-semibold text-muted mb-0" data-text="tabs.categoryCorrelations.indicator"></label>
                            <select id="corr-indicator" class="form-select form-select-sm" style="min-width:230px"></select>
                        </div>
                    </div>
                </div>
                <div class="card-body p-4">
                    <div class="row g-4 align-items-start">
                        <div class="col-lg-9">
                            <div class="chart-wrap" style="height:500px"><canvas id="chart-corr"></canvas></div>
                        </div>
                        <div class="col-lg-3">
                            <div id="corr-stats" class="p-3 rounded" style="background:#f8f9fa;border:1px solid #dee2e6;font-size:0.88rem">
                                <div class="text-muted small mb-2 fw-semibold" data.text="tabs.categoryCorrelations.stats"></div>
                                <table class="table table-sm mb-0" style="font-size:0.88rem">
                                    <tbody id="corr-stats-body"></tbody>
                                </table>
                            </div>
                            <div class="mt-3 small text-muted">
                                <strong data-text="tabs.categoryCorrelations.xAxis"></strong> <span data-text="tabs.categoryCorrelations.xAxisLabel"></span><br><br>
                                <strong data-text="tabs.categoryCorrelations.yAxis"></strong> <span data-text="tabs.categoryCorrelations.yAxisLabel"></span><br><br>
                                <span data-text="tabs.categoryCorrelations.notice"></span>
                            </div>
                        </div>
                    </div>
                    <div id="corr-loading" class="text-center py-5 text-muted" style="display:none">
                        <div class="spinner-border spinner-border-sm me-2"></div><span data-text="tabs.categoryCorrelations.loader"></span>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Topic Profiles (heatmap) ── -->
        <div class="tab-pane fade" id="tab-heatmap">
            <div id="topics-loading" class="text-center py-5 text-muted" style="display:none">
                <div class="spinner-border spinner-border-sm me-2"></div><span data-text="tabs.topicProfiles.loader"></span>
            </div>
            <div class="card" id="topics-heatmap-card" style="display:none">
                <div class="card-header d-flex flex-wrap align-items-center gap-3 py-3">
                    <strong data-text="tabs.topicProfiles.title"></strong>
                    <div class="d-flex align-items-center gap-2 ms-auto flex-wrap">
                        <label class="small fw-semibold text-muted mb-0" data-text="tabs.topicProfiles.epGroup"></label>
                        <select id="topic-filter-group"   class="form-select form-select-sm" style="min-width:120px"></select>
                        <label class="small fw-semibold text-muted mb-0" data-text="tabs.topicProfiles.country"></label>
                        <select id="topic-filter-country" class="form-select form-select-sm" style="min-width:100px"></select>
                        <span class="stat-badge ms-1" id="topics-heatmap-stat"></span>
                    </div>
                </div>
                <div class="card-body p-3">
                    <div id="topic-heatmap-container" class="overflow-auto" style="max-height:520px"></div>
                    <div class="mt-3 small d-flex align-items-center gap-2 flex-wrap">
                        <span class="fw-semibold text-muted" data-text="tabs.topicProfiles.yesFraction"></span>
                        <span style="background:hsl(0,70%,45%);color:white;padding:2px 10px;border-radius:4px">0% <span data-text="tabs.topicProfiles.yes"></span></span>
                        <span style="background:hsl(60,70%,42%);color:white;padding:2px 10px;border-radius:4px">50%</span>
                        <span style="background:hsl(120,65%,35%);color:white;padding:2px 10px;border-radius:4px">100% <span data-text="tabs.topicProfiles.yes"></span></span>
                        <span class="text-muted ms-2" data-text="tabs.topicProfiles.notice"></span>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Deviation Rules ── -->
        <div class="tab-pane fade" id="tab-deviation">
            <div class="card">
                <div class="card-header py-3 d-flex align-items-center gap-2 flex-wrap">
                    <strong data-text="tabs.deviationRules.title"></strong>
                    <span class="stat-badge ms-auto" id="deviation-stat"></span>
                    <span class="text-muted small" data-text="tabs.deviationRules.subtitle"></span>
                </div>
                <div class="card-body p-3">
                    <table id="table-deviation" class="table table-sm table-striped table-hover" style="width:100%">
                        <thead>
                            <tr>
                                <th data-text="tabs.deviationRules.table.if"></th>
                                <th data-text="tabs.deviationRules.table.then"></th>
                                <th data-text="tabs.deviationRules.table.support"></th>
                                <th data-text="tabs.deviationRules.table.confidence"></th>
                                <th data-text="tabs.deviationRules.table.lift"></th>
                                <th data-text="tabs.deviationRules.table.parties"></th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                    <div class="mt-2 small text-muted">
                        <strong data-text="tabs.deviationRules.above"></strong> = <span data-text="tabs.deviationRules.aboveLabel"></span> &nbsp;|&nbsp;
                        <strong data-text="tabs.deviationRules.below"></strong> = <span data-text="tabs.deviationRules.belowLabel"></span> &nbsp;|&nbsp;
                        <strong data-text="tabs.deviationRules.lift"></strong> = <span data-text="tabs.deviationRules.liftLabel"></span>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Opposition Rules ── -->
        <div class="tab-pane fade" id="tab-opposition">
            <div class="card">
                <div class="card-header py-3 d-flex align-items-center gap-2 flex-wrap">
                    <strong data-text="tabs.oppositionRules.title"></strong>
                    <span class="stat-badge ms-auto" id="opposition-stat"></span>
                    <span class="text-muted small" data-text="tabs.oppositionRules.subtitle"></span>
                </div>
                <div class="card-body p-3">
                    <table id="table-opposition" class="table table-sm table-striped table-hover" style="width:100%">
                        <thead>
                            <tr>
                                <th data-text="tabs.oppositionRules.table.if"></th>
                                <th data-text="tabs.oppositionRules.table.then"></th>
                                <th data-text="tabs.oppositionRules.table.support"></th>
                                <th data-text="tabs.oppositionRules.table.confidence"></th>
                                <th data-text="tabs.oppositionRules.table.lift"></th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                    <div class="mt-2 small text-muted">
                        <strong data-text="tabs.oppositionRules.for"></strong> = <span data-text="tabs.oppositionRules.forLabel"></span> &nbsp;|&nbsp;
                        <strong data-text="tabs.oppositionRules.against"></strong> = <span data-text="tabs.oppositionRules.againstLabel"></span> &nbsp;|&nbsp;
                        <strong data-text="tabs.oppositionRules.abstain"></strong> = <span data-text="tabs.oppositionRules.abstainLabel"></span> &nbsp;|&nbsp;
                        <strong data-text="tabs.oppositionRules.lift"></strong> = <span data-text="tabs.oppositionRules.liftLabel"></span>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── MEP Comparison ── -->
        <div class="tab-pane fade" id="tab-compare">
            <div class="card">
                <div class="card-header py-3">
                    <strong data-text="tabs.mepComparison.title"></strong>
                </div>
                <div class="card-body p-3">
                    <div class="accordion accordion-flush" id="acc-compare">

                        <!-- Panel 1: Custom Comparison (default open) -->
                        <div class="accordion-item">
                            <h2 class="accordion-header">
                                <button class="accordion-button" type="button"
                                        data-bs-toggle="collapse" data-bs-target="#acc-custom">
                                    <span data-text="tabs.mepComparison.customTitle"></span>
                                    <span class="text-muted small ms-2" data-text="tabs.mepComparison.customSubtitle"></span>
                                </button>
                            </h2>
                            <div id="acc-custom" class="accordion-collapse collapse show">
                                <div class="accordion-body p-4">
                                    <div class="row g-4 mb-4">
                                        <div class="col-md-5">
                                            <label class="form-label fw-semibold small text-muted text-uppercase" data-text="tabs.mepComparison.mepA"></label>
                                            <input type="text" id="search-mep1" class="form-control form-control-sm mb-1" data-placeholder="tabs.mepComparison.placeholder" autocomplete="off">
                                            <select id="sel-mep1" class="form-select form-select-sm" size="7"></select>
                                        </div>
                                        <div class="col-md-2 d-flex align-items-center justify-content-center pt-4">
                                            <button id="btn-compare" class="btn btn-primary px-4" disabled data-text="tabs.mepComparison.compare"></button>
                                        </div>
                                        <div class="col-md-5">
                                            <label class="form-label fw-semibold small text-muted text-uppercase" data-text="tabs.mepComparison.mepB"></label>
                                            <input type="text" id="search-mep2" class="form-control form-control-sm mb-1" data-placeholder="tabs.mepComparison.placeholder" autocomplete="off">
                                            <select id="sel-mep2" class="form-select form-select-sm" size="7"></select>
                                        </div>
                                    </div>
                                    <div id="compare-loading" class="text-center py-4 text-muted" style="display:none">
                                        <div class="spinner-border spinner-border-sm me-2"></div><span data-text="tabs.mepComparison.loader"></span>
                                    </div>
                                    <div id="compare-result" style="display:none"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Panel 2: All-Pairs Ranking (default collapsed) -->
                        <div class="accordion-item">
                            <h2 class="accordion-header">
                                <button class="accordion-button collapsed" type="button"
                                        data-bs-toggle="collapse" data-bs-target="#acc-rankings">
                                    <span data-text="tabs.mepComparison.rankingsTitle"></span>
                                    <span class="text-muted small ms-2" data-text="tabs.mepComparison.rankingsSubtitle"></span>
                                </button>
                            </h2>
                            <div id="acc-rankings" class="accordion-collapse collapse">
                                <div class="accordion-body">
                                    <div class="d-flex align-items-center gap-2 mb-3">
                                        <label class="small fw-semibold text-muted mb-0" data-text="tabs.mepComparison.rankingsView"></label>
                                        <select id="pair-view-select" class="form-select form-select-sm" style="width:auto"></select>
                                        <span class="stat-badge ms-auto" id="pair-rankings-stat"></span>
                                    </div>
                                    <div id="pair-rankings-loading" class="text-center py-4 text-muted" style="display:none">
                                        <div class="spinner-border spinner-border-sm me-2"></div>
                                        <span data-text="tabs.mepComparison.rankingsLoader"></span>
                                    </div>
                                    <div id="pair-rankings-nodata" class="alert alert-info" style="display:none"
                                         data-text="tabs.mepComparison.rankingsNoData"></div>
                                    <div id="pair-rankings-content" style="display:none">
                                        <table id="table-pair-rankings" class="table table-sm table-striped table-hover" style="width:100%">
                                            <thead><tr>
                                                <th data-text="tabs.mepComparison.table.rank"></th>
                                                <th data-text="tabs.mepComparison.table.mepA"></th>
                                                <th data-text="tabs.mepComparison.table.mepB"></th>
                                                <th data-text="tabs.mepComparison.table.agreement"></th>
                                                <th data-text="tabs.mepComparison.table.commonVotes"></th>
                                            </tr></thead>
                                            <tbody></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        </div>

                    </div><!-- .accordion -->
                </div>
            </div>
        </div>

        <!-- ── Member Statistics ── -->
        <div class="tab-pane fade" id="tab-mstats">
            <div id="mstats-loading" class="text-center py-5 text-muted" style="display:none">
                <div class="spinner-border spinner-border-sm me-2"></div><span data-text="tabs.memberStats.loader"></span>
            </div>
            <div class="row g-4" id="mstats-content" style="display:none">
                <div class="col-lg-4">
                    <div class="card h-100">
                        <div class="card-header py-3"><strong data-text="tabs.memberStats.graphTitle"></strong></div>
                        <div class="card-body p-4">
                            <div class="chart-wrap" style="height:280px"><canvas id="chart-mstats"></canvas></div>
                        </div>
                    </div>
                </div>
                <div class="col-lg-8">
                    <div class="card">
                        <div class="card-header py-3 d-flex align-items-center gap-2">
                            <strong data-text="tabs.memberStats.tableTitle"></strong>
                            <span class="stat-badge ms-auto" id="mstats-stat"></span>
                        </div>
                        <div class="card-body p-3">
                            <table id="table-mstats" class="table table-sm table-striped table-hover" style="width:100%">
                                <thead>
                                    <tr>
                                        <th data-text="tabs.memberStats.table.name"></th>
                                        <th data-text="tabs.memberStats.table.country"></th>
                                        <th data-text="tabs.memberStats.table.party"></th>
                                        <th data-text="tabs.memberStats.table.for"></th>
                                        <th data-text="tabs.memberStats.table.against"></th>
                                        <th data-text="tabs.memberStats.table.abstain"></th>
                                        <th data-text="tabs.memberStats.table.total"></th>
                                    </tr>
                                </thead>
                                <tbody></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Subject Statistics ── -->
        <div class="tab-pane fade" id="tab-sstats">
            <div id="sstats-loading" class="text-center py-5 text-muted" style="display:none">
                <div class="spinner-border spinner-border-sm me-2"></div><span data-text="tabs.subjectStats.loader"></span>
            </div>
            <div id="sstats-content" style="display:none">
                <div class="card mb-4">
                    <div class="card-header py-3 d-flex align-items-center gap-2">
                        <strong data-text="tabs.subjectStats.factionsTitle"></strong>
                        <span class="stat-badge ms-auto" id="sstats-factions-stat"></span>
                    </div>
                    <div class="card-body p-4">
                        <div class="chart-wrap" style="height:320px"><canvas id="chart-sstats-factions"></canvas></div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-header py-3 d-flex align-items-center gap-2">
                        <strong data-text="tabs.subjectStats.partiesTitle"></strong>
                        <span class="stat-badge ms-auto" id="sstats-parties-stat"></span>
                    </div>
                    <div class="card-body p-3">
                        <table id="table-sstats" class="table table-sm table-striped table-hover" style="width:100%">
                            <thead>
                                <tr>
                                    <th data-text="tabs.subjectStats.table.party"></th>
                                    <th data-text="tabs.subjectStats.table.for"></th>
                                    <th data-text="tabs.subjectStats.table.against"></th>
                                    <th data-text="tabs.subjectStats.table.abstain"></th>
                                    <th data-text="tabs.subjectStats.table.total"></th>
                                    <th data-text="tabs.subjectStats.table.forPct"></th>
                                </tr>
                            </thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Anomaly Detection ── -->
        <div class="tab-pane fade" id="tab-anomaly">
            <div class="card">
                <div class="card-header py-3">
                    <strong data-text="tabs.anomalyDetection.title"></strong>
                    <span class="text-muted small ms-2" data-text="tabs.anomalyDetection.subtitle"></span>
                </div>
                <div class="card-body p-4">
                    <div class="row g-3 mb-4 align-items-end">
                        <div class="col-md-8">
                            <label class="form-label fw-semibold small text-muted text-uppercase" data-text="tabs.anomalyDetection.mep"></label>
                            <input type="text" id="anomaly-search" class="form-control form-control-sm mb-1" data-placeholder="tabs.anomalyDetection.placeholder" autocomplete="off">
                            <select id="anomaly-sel" class="form-select form-select-sm" size="5"></select>
                        </div>
                        <div class="col-md-4 d-flex align-items-end">
                            <button id="btn-anomaly" class="btn btn-primary px-4" disabled data-text="tabs.anomalyDetection.detect"></button>
                        </div>
                    </div>
                    <div id="anomaly-loading" class="text-center py-4 text-muted" style="display:none">
                        <div class="spinner-border spinner-border-sm me-2"></div><span data-text="tabs.anomalyDetection.loader"></span>
                    </div>
                    <div id="anomaly-result" style="display:none"></div>
                </div>
            </div>
        </div>

    </div><!-- tab-content -->
</div><!-- container -->

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/dataTables.bootstrap5.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<script src="assets/js/library.js"></script>
<script src="assets/js/app.js"></script>
</body>
</html>
