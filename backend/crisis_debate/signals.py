"""Situational signal sources (planner.md §3.2).

Structured fetches, not browser automation. Each adapter turns a public API into
`SituationalFact`s that a human then accepts or rejects before they reach a run.

**Shapes here were read off live responses, not recalled.** That distinction
matters: ReliefWeb's v1 API returns 410 Gone and v2 returns 403 without a
registered appname, which a from-memory implementation would have shipped
broken. GDACS and the World Bank were verified returning 200 with the fields
used below.

    GDACS        disasters and hazards, GeoJSON. No key. VERIFIED
    World Bank   fiscal and macro indicators, JSON. No key. VERIFIED
    ReliefWeb    humanitarian reporting. NEEDS a registered appname — disabled
                 by default rather than shipped broken
    Manual       what the analyst types. Always available, and often the fastest
                 route to the truth (planner.md §3.2)

Every adapter fails soft: no network, a timeout or a changed shape yields no
facts and a recorded error, never a raised exception into a run. A situational
fetch is a convenience; losing it must not stop a negotiation being rehearsed.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from crisis_debate.provenance import Source
from crisis_debate.situational import ConditionsSnapshot, SituationalFact

DEFAULT_TIMEOUT = 8.0
_UA = {"User-Agent": "dara/0.1 (crisis negotiation rehearsal; research use)"}


def _ctx() -> Optional[ssl.SSLContext]:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _get_json(url: str, timeout: float) -> object:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SourceResult:
    name: str
    facts: List[SituationalFact] = field(default_factory=list)
    error: str = ""
    ms: float = 0.0


# --------------------------------------------------------------------------
# GDACS — disasters. Verified live.
# --------------------------------------------------------------------------
_GDACS_TYPES = {"EQ": "earthquake", "TC": "tropical cyclone", "FL": "flood",
                "VO": "volcanic activity", "DR": "drought", "WF": "wildfire"}
# Alert level drives confidence: GDACS's own triage, reused rather than invented.
_GDACS_CONF = {"Red": 0.9, "Orange": 0.6, "Green": 0.3}


def gdacs(country: str, timeout: float = DEFAULT_TIMEOUT, limit: int = 8) -> SourceResult:
    t0 = datetime.now()
    out = SourceResult(name="GDACS")
    try:
        url = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?"
               + urllib.parse.urlencode({"country": country, "limit": limit}))
        data = _get_json(url, timeout)
        for feat in (data or {}).get("features", [])[:limit]:
            p = feat.get("properties", {}) or {}
            kind = _GDACS_TYPES.get(p.get("eventtype", ""), p.get("eventtype", "event"))
            when = (p.get("fromdate") or "")[:10]
            sev = (p.get("severitydata") or {}).get("severitytext", "")
            urls = p.get("url") or {}
            out.facts.append(SituationalFact(
                party=country,
                summary=" ".join(x for x in [p.get("alertlevel", ""), kind,
                                             p.get("eventname", ""), sev] if x).strip(),
                as_of=when,
                source=Source(source_id=f"gdacs-{p.get('eventid','')}",
                              title=p.get("htmldescription") or f"{kind} in {country}",
                              url=urls.get("report", ""), published=when,
                              retrieved_at=_now(), who_said_it="GDACS"),
                # Effect is left unset on purpose: which issue a disaster
                # constrains is a judgement the human makes when accepting it.
                effect="none",
                confidence=_GDACS_CONF.get(p.get("alertlevel", ""), 0.3)))
    except Exception as e:                      # fail soft — never break a run
        out.error = f"{type(e).__name__}: {e}"
    out.ms = (datetime.now() - t0).total_seconds() * 1000
    return out


# --------------------------------------------------------------------------
# World Bank — fiscal capacity. Verified live.
# --------------------------------------------------------------------------
_WB_INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": "GDP growth (annual %)",
    "GC.DOD.TOTL.GD.ZS": "central government debt (% of GDP)",
    "FP.CPI.TOTL.ZG": "inflation, consumer prices (annual %)",
}


def world_bank(country_iso3: str, party: str, timeout: float = DEFAULT_TIMEOUT,
               indicators: Optional[Dict[str, str]] = None) -> SourceResult:
    t0 = datetime.now()
    out = SourceResult(name="World Bank")
    try:
        for code, label in (indicators or _WB_INDICATORS).items():
            url = (f"https://api.worldbank.org/v2/country/{country_iso3}/indicator/{code}"
                   f"?format=json&per_page=60")
            data = _get_json(url, timeout)
            rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
            latest = next((r for r in rows if r.get("value") is not None), None)
            if not latest:
                continue
            out.facts.append(SituationalFact(
                party=party,
                summary=f"{label}: {latest['value']:.2f} ({latest['date']})",
                as_of=str(latest["date"]),
                source=Source(source_id=f"wb-{code}",
                              title=f"World Bank — {label}",
                              url=f"https://data.worldbank.org/indicator/{code}?locations={country_iso3}",
                              published=str(latest["date"]), retrieved_at=_now(),
                              who_said_it="World Bank"),
                effect="none", confidence=0.8))
    except Exception as e:
        out.error = f"{type(e).__name__}: {e}"
    out.ms = (datetime.now() - t0).total_seconds() * 1000
    return out


# --------------------------------------------------------------------------
# ReliefWeb — needs a registered appname. Off by default.
# --------------------------------------------------------------------------
def reliefweb(country: str, appname: str = "", timeout: float = DEFAULT_TIMEOUT) -> SourceResult:
    out = SourceResult(name="ReliefWeb")
    if not appname:
        out.error = ("disabled: ReliefWeb v1 returns 410 Gone and v2 returns 403 "
                     "without a registered appname. Register one and pass it.")
        return out
    try:
        url = ("https://api.reliefweb.int/v2/disasters?"
               + urllib.parse.urlencode({"appname": appname, "limit": 5}))
        _get_json(url, timeout)
        out.error = "shape not yet verified against a live authorised response"
    except Exception as e:
        out.error = f"{type(e).__name__}: {e}"
    return out


# --------------------------------------------------------------------------
# Manual — the analyst types what they know
# --------------------------------------------------------------------------
def manual(party: str, text: str, issue: str = "", option: str = "",
           effect: str = "none", as_of: str = "") -> SourceResult:
    """Often the fastest and most reliable route: someone who has heard about a
    coup or a flood knows it before any index does."""
    f = SituationalFact(
        party=party, summary=text, as_of=as_of or _now()[:10],
        source=Source(source_id="manual", title="Entered by analyst",
                      published=as_of or _now()[:10], retrieved_at=_now(),
                      who_said_it="analyst"),
        effect=effect if effect in ("constrain", "reweight", "none") else "none",
        issue=issue, option=option, confidence=0.9, accepted=True)
    return SourceResult(name="Manual", facts=[f])


# --------------------------------------------------------------------------
# Parallel fetch -> an unaccepted snapshot awaiting human review
# --------------------------------------------------------------------------
def fetch_conditions(scenario_id: str, parties: Sequence[tuple],
                     timeout: float = DEFAULT_TIMEOUT,
                     reliefweb_appname: str = "") -> tuple:
    """`parties` is a sequence of (party_name, iso3). Returns
    (ConditionsSnapshot, [SourceResult]).

    Facts arrive **unaccepted**: nothing reaches a run until a human accepts it
    (planner.md §5 Phase 2). Sources are queried in parallel, and a source that
    fails contributes an error rather than an exception.
    """
    jobs: List[Callable[[], SourceResult]] = []
    for name, iso3 in parties:
        jobs.append(lambda n=name: gdacs(n, timeout))
        if iso3:
            jobs.append(lambda i=iso3, n=name: world_bank(i, n, timeout))
    if reliefweb_appname:
        jobs.append(lambda: reliefweb(parties[0][0], reliefweb_appname, timeout))

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as pool:
        results = list(pool.map(lambda f: f(), jobs))

    facts = [f for r in results for f in r.facts]
    snap = ConditionsSnapshot(
        scenario_id=scenario_id, fetched_at=_now(), facts=facts,
        sources_queried=[f"{r.name}{' (' + r.error + ')' if r.error else ''}" for r in results])
    return snap, results
