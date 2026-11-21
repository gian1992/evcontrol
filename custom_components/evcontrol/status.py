"""Human-readable status for EV Control.

The planner already explains itself, but in its own terms: reserves, headroom,
share factors. That text is the right thing to read when something looks wrong and
you are debugging the control law. It is the wrong thing to read when you walk past
the tablet and want to know whether the car will be ready.

This module answers three questions in plain language, and nothing else:

  * **Cosa sta facendo** -- one sentence, the current action.
  * **Perche'** -- one sentence, the reason for that action.
  * **Ce la fa?** -- will the car be charged by the deadline, and what is the
    latest moment it can still fall back to the grid and make it.

That last one is the reassuring number. Knowing "if the sun does not show up, at
03:20 I start pulling from the grid and the car is full by 07:00" is what makes it
possible to stop watching the graphs.

Pure functions, no Home Assistant imports, so the phrasing can be tested directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from .smart_charging import (
    CHARGING_EFFICIENCY,
    STATE_BLOCKED,
    STATE_CHARGING,
    STATE_HOLD,
    STATE_IDLE,
    STATE_STOPPING,
    STATE_WAITING,
    SUN_DARK,
    SUN_DAY,
    SUN_SETTING,
    TREND_RISING,
    URGENCY_CRITICAL,
    URGENCY_RELAXED,
    URGENCY_TIGHT,
)

_LOGGER = logging.getLogger(__name__)

# Outlook verdicts. Kept short so they read well as a sensor state.
OUTLOOK_DONE = "completata"
OUTLOOK_ON_TIME = "in orario"
OUTLOOK_GRID_SOON = "passo alla rete"
OUTLOOK_CHARGING_GRID = "carico da rete"
OUTLOOK_LATE = "in ritardo"
OUTLOOK_NO_TARGET = "nessun target"
OUTLOOK_PAUSED = "in pausa"
OUTLOOK_UNKNOWN = "dati insufficienti"


def _target_word(target_soc: float) -> str:
    """"piena" when the deadline wants it all, "all'80%" when it does not."""
    return "piena" if target_soc >= 99.5 else f"al {target_soc:.0f}%"


def _target_adj(target_soc: float) -> str:
    """Same thing, in the position an adjective takes: "e' piena" / "e' all'80%"."""
    return "piena" if target_soc >= 99.5 else f"all'{target_soc:.0f}%"


def _hhmm(moment: Optional[datetime]) -> str:
    return moment.strftime("%H:%M") if moment else "—"


def _durata(hours: float) -> str:
    """A duration a person would say out loud: '3 h 20 min', '45 min', '2 giorni 20 h'."""
    if hours <= 0:
        return "0 min"
    total_min = int(round(hours * 60))
    if total_min < 1:
        return "meno di 1 min"
    if total_min < 60:
        return f"{total_min} min"
    h, m = divmod(total_min, 60)
    if h >= 24:
        # Past a day nobody counts minutes, and "68 h" has to be divided by hand.
        d, h = divmod(h, 24)
        giorni = "1 giorno" if d == 1 else f"{d} giorni"
        return giorni if h == 0 else f"{giorni} {h} h"
    return f"{h} h" if m == 0 else f"{h} h {m} min"


_GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")


def _quando(moment: datetime, now: datetime, today_prefix: str = "le") -> str:
    """A moment as said out loud: 'le 07:00' today, 'lunedì alle 07:00' otherwise.

    A bare "07:00" is ambiguous as soon as the target is not today's, and reads
    as "this morning" -- which is exactly how a Monday target looks urgent on a
    Friday. `today_prefix` is the article the sentence wants for today ("le",
    "alle", or nothing).
    """
    hhmm = moment.strftime("%H:%M")
    days = (moment.date() - now.date()).days
    if days == 0:
        return f"{today_prefix} {hhmm}".strip()
    if days == 1:
        giorno = "domani"
    elif 1 < days < 7:
        giorno = _GIORNI[moment.weekday()]
    else:
        giorno = moment.strftime("%d/%m")
    return f"{giorno} alle {hhmm}"


def _num(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def _phrase_end(text: str) -> str:
    """Sentences get concatenated here; make sure they end like sentences."""
    text = text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _kw(watts: float) -> str:
    return f"{watts / 1000:.1f} kW".replace(".", ",")


@dataclass
class ChargeStatus:
    """What the three sensors publish."""

    headline: str = ""          # cosa sta facendo
    detail: str = ""            # perche'
    outlook: str = OUTLOOK_UNKNOWN
    outlook_detail: str = ""
    grid_fallback_at: Optional[datetime] = None   # quando, al piu' tardi, si attacca alla rete
    full_at: Optional[datetime] = None            # quando sara' piena, al ritmo attuale
    attributes: Dict[str, Any] = field(default_factory=dict)


# ── Cosa sta facendo, e perche' ──────────────────────────────────────────────

def _describe_action(
    now: datetime,
    decision,
    signals,
    cfg,
    state,
    mode: str,
    charging_enabled: bool,
    ev_soc: Optional[float] = None,
    phase1_soc: Optional[float] = None,
) -> tuple:
    """Return (headline, detail) for the planner's current state."""
    amps = decision.amps
    volts = max(signals.voltage, 1.0)
    soc = signals.battery_soc
    batt_w = signals.battery_w

    if not charging_enabled:
        return ("Ricarica disattivata", "L'hai messa in pausa tu. Riattivala per farla ripartire.")

    if decision.state_name == STATE_CHARGING:
        head = f"Carica a {amps:.0f} A ({_kw(amps * volts)})"
        if phase1_soc is not None:
            # AUTO phase 1: a buffer charge, deliberately indifferent to the sun and
            # to the deadline. Saying "la scadenza è vicina" here was simply false.
            auto = f"l'auto è al {ev_soc:.0f}%," if ev_soc is not None else "l'auto è"
            why = (f"Carica di base: {auto} sotto il {phase1_soc:.0f}% che AUTO garantisce "
                   f"comunque, sole o no. Arrivata al {phase1_soc:.0f}% torno ad aspettare "
                   f"il sole.")
        elif batt_w > 100:
            why = f"Dal sole. Alla batteria di casa vanno {batt_w:.0f} W, in rete non va niente."
        elif decision.urgency == URGENCY_CRITICAL:
            why = ("Sole e rete insieme: la scadenza è vicina e il sole da solo non basta "
                   "più a farcela.")
        elif soc >= 97:
            why = "Dal sole. La batteria di casa è piena, quindi tutto l'avanzo va all'auto."
        else:
            why = f"Dal sole. La batteria di casa è al {soc:.0f}%."
        return head, why

    if decision.state_name == STATE_HOLD:
        head = f"Tengo il minimo, {amps:.0f} A"
        coda = (f"Il sole c'è ancora, quindi non stacco: staccare e riattaccare è "
                f"esattamente quello che manda in blocco l'auto. Continuo finché la "
                f"batteria di casa resta sopra il {cfg.solar_min_soc:.0f}%.")
        # Two different reasons land in HOLD: the house eating the surplus, and
        # production itself being low without the sun going down. The second one
        # is the interesting one, so it says why it is not calling it a day.
        if decision.sun_phase == SUN_DAY and "Produzione bassa" in decision.reason:
            why = (f"Il fotovoltaico rende poco: {decision.sun_reason}. "
                   f"Tengo il minimo e la differenza la mette "
                   f"la batteria di casa ({soc:.0f}%), finché resta sopra il "
                   f"{cfg.solar_min_soc:.0f}%.")
        elif batt_w < -50:
            why = (f"La casa sta consumando tutto il fotovoltaico e la differenza la mette "
                   f"la batteria ({abs(batt_w):.0f} W, al {soc:.0f}%). {coda}")
        else:
            why = f"La casa sta consumando quasi tutto il sole. Batteria al {soc:.0f}%. {coda}"
        return head, why

    if decision.state_name == STATE_WAITING:
        left = decision.dwell_left_s
        head = "Pronta a partire"
        why = (f"C'è abbastanza sole e la colonnina è ancora ferma. Aspetto "
               f"{_durata(left / 3600)} per essere sicuro che regga, poi attacco: "
               f"partire e doversi fermare subito è la cosa peggiore che posso fare.")
        return head, why

    if decision.state_name == STATE_STOPPING and state.charging:
        head = "Sto per fermarmi"
        why = decision.reason
        return head, why

    if decision.state_name == STATE_STOPPING:
        return ("Ferma", decision.reason)

    if decision.state_name == STATE_BLOCKED:
        head = "In attesa"
        reason = decision.reason
        if "budget stop" in reason:
            why = (f"Ho già fatto {decision.stops_today} stacca/attacca oggi, che è il massimo "
                   f"che mi sono dato. Riprendo domani, o subito se la scadenza diventa "
                   f"critica, o adesso se premi Sblocca.")
        elif "pausa minima" in reason:
            # The time itself is added by `_when_start`, which owns every
            # "and when does it start" sentence; saying it twice reads badly.
            why = ("Ho staccato da poco: riaccendere subito è il modo più veloce "
                   "per far bloccare l'auto.")
        elif "in calo" in reason:
            why = ("Il sole sta calando. Partire adesso vorrebbe dire fermarsi fra poco, "
                   "e spenderei uno stacca/attacca per pochi minuti di carica.")
        elif "finestra troppo corta" in reason:
            why = ("Fra mezz'ora la previsione non dà abbastanza sole per tenere la carica. "
                   "Aspetto una finestra che valga la pena.")
        else:
            why = reason
        return head, why

    # STATE_IDLE
    head = "In attesa di sole"
    why = (f"Parto appena c'è surplus per {cfg.charger_min_current:.0f} A "
           f"({cfg.charger_min_current * volts:.0f} W) e la batteria di casa è almeno "
           f"al {cfg.solar_min_soc:.0f}%. Poi tengo il minimo finché i pannelli "
           f"producono qualcosa.")
    if decision.trend == TREND_RISING:
        why += " Il sole sta salendo."
    return head, why


# ── Quando parte ─────────────────────────────────────────────────────────────

def _when_start(now, decision, signals, cfg, state) -> Optional[str]:
    """One sentence answering "e quindi quando parte?", or None if unknowable.

    Every blocked state knows something about its own release condition -- a timer
    that expires, a battery that is refilling at a measurable rate -- and stating
    it turns "in attesa" from a shrug into a plan. Where the condition genuinely
    cannot be dated (the sun has to come back), it says that instead of inventing
    a time.
    """
    if decision.state_name == STATE_WAITING and decision.dwell_left_s > 0:
        return (f"Se il surplus regge attacco fra "
                f"{_durata(decision.dwell_left_s / 3600)}.")

    reason = decision.reason or ""

    if "pausa minima" in reason and state.last_stop is not None:
        resume = state.last_stop + timedelta(minutes=cfg.min_off_min)
        left = (resume - now).total_seconds() / 3600.0
        if left > 0:
            return f"Non riparto prima delle {_hhmm(resume)}, fra {_durata(left)}."

    if "batteria di casa" in reason and "sotto il" in reason:
        soc = signals.battery_soc
        missing_kwh = max(0.0, cfg.solar_min_soc - soc) / 100.0 * cfg.home_battery_capacity_kwh
        # Only the charge rate the battery is actually taking right now can date
        # this. Discharging or idle, the honest answer is "when the sun returns".
        if signals.battery_w > 100 and missing_kwh > 0:
            hours = missing_kwh / (signals.battery_w / 1000.0)
            if 0 < hours < 24:
                return (f"Alla batteria di casa mancano {_num(missing_kwh)} kWh per tornare "
                        f"al {cfg.solar_min_soc:.0f}%: a {signals.battery_w:.0f} W ci "
                        f"arriva verso le {_hhmm(now + timedelta(hours=hours))}, fra "
                        f"{_durata(hours)}. Da lì posso partire.")
        return (f"Riparto quando la batteria di casa risale al {cfg.solar_min_soc:.0f}% "
                f"(ora {soc:.0f}%) e c'è surplus per {cfg.charger_min_current:.0f} A. "
                f"Adesso non si sta caricando, quindi serve prima il sole.")

    if "budget stop" in reason:
        return "Riprendo domani, o subito se la scadenza diventa critica."

    if decision.state_name == STATE_IDLE:
        available = decision.headroom_w + decision.reserve_w
        needed = cfg.charger_min_current * max(signals.voltage, 1.0)
        if needed > available:
            return (f"Mancano {needed - available:.0f} W di surplus per partire a "
                    f"{cfg.charger_min_current:.0f} A.")
    return None


# ── Ce la fa? ────────────────────────────────────────────────────────────────

def _describe_outlook(
    now: datetime,
    decision,
    signals,
    need,
    cfg,
    target_label: Optional[str],
    reference_current: float,
    charging_enabled: bool,
    phase1_soc: Optional[float] = None,
    plan_start_at: Optional[datetime] = None,
) -> tuple:
    """Return (outlook, detail, grid_fallback_at, full_at, extra_attributes)."""
    volts = max(signals.voltage, 1.0)
    extra: Dict[str, Any] = {}

    if not charging_enabled:
        return (OUTLOOK_PAUSED,
                "La ricarica è disattivata, quindi non posso garantire nessuna scadenza. "
                "Riattivala e ricalcolo.",
                None, None, extra)

    if need.ev_soc is None:
        return (OUTLOOK_UNKNOWN,
                "Non ho il livello di carica dell'auto, quindi non posso dire se ce la fa. "
                "Serve il sensore SOC del veicolo.",
                None, None, extra)

    # Done means the deadline has what it asked for, not 100%. The cap at 99
    # stays for a 100% target: CC/CV tapering means the last point can take as
    # long as the ten before it, and the car often never reports a round 100.
    done_at = min(99.0, need.target_soc - 0.5)
    if need.ev_soc >= done_at:
        if need.target_soc < 99.5:
            return (OUTLOOK_DONE,
                    f"L'auto è al {need.ev_soc:.0f}%: l'obiettivo era "
                    f"{need.target_soc:.0f}%.",
                    None, None, extra)
        return (OUTLOOK_DONE, f"L'auto è al {need.ev_soc:.0f}%.", None, None, extra)

    # Time to full at the current rate, whatever that rate happens to be.
    full_at = None
    if signals.ev_w > 200:
        kwh_left = need.energy_needed_kwh()
        hours_left = kwh_left / (signals.ev_w / 1000.0)
        if 0 < hours_left < 48:
            full_at = now + timedelta(hours=hours_left)
            extra["al_ritmo_attuale"] = _durata(hours_left)

    if need.seconds_to_target is None:
        detail = "Nessun orario impostato per oggi: carico quando c'è sole, senza scadenza."
        if full_at:
            detail += (f" Al ritmo attuale sarebbe {_target_adj(need.target_soc)} "
                       f"alle {_hhmm(full_at)}.")
        return (OUTLOOK_NO_TARGET, detail, None, full_at, extra)

    hours_available = need.seconds_to_target / 3600.0
    hours_needed = need.time_needed_h(volts)
    hours_needed_margin = hours_needed * 1.10
    target_at = now + timedelta(hours=hours_available)

    # The reassuring number: the last moment at which falling back to the grid
    # still gets the car full by the deadline.
    grid_fallback_at = target_at - timedelta(hours=hours_needed_margin)
    # The last moment the deadline allows, unless the plan means to start before
    # it. Earlier is the honest answer: that is the one that will happen.
    if plan_start_at is not None and plan_start_at < grid_fallback_at:
        grid_fallback_at = plan_start_at
    when = _quando(target_at, now)

    extra.update({
        "target": _quando(target_at, now, ""),
        "tempo_disponibile": _durata(hours_available),
        "tempo_necessario": _durata(hours_needed),
        "energia_mancante_kwh": round(need.energy_needed_kwh(), 1),
        "target_soc": round(need.target_soc),
    })

    # Can it still be done at all, even flat out on the grid?
    hours_at_max = 0.0
    max_kw = cfg.charger_max_current * volts / 1000.0
    if max_kw > 0:
        hours_at_max = need.energy_needed_kwh() / max_kw
    if hours_available < hours_at_max:
        reachable = need.ev_soc + (
            hours_available * max_kw * CHARGING_EFFICIENCY / max(need.ev_capacity_kwh, 1) * 100
        )
        extra["soc_previsto_al_target"] = round(min(100.0, reachable))
        return (
            OUTLOOK_LATE,
            f"Non ci arrivo: entro {when} mancano {_durata(hours_available)} e ne servirebbero "
            f"{_durata(hours_at_max)} anche a {cfg.charger_max_current:.0f} A. "
            f"Arriverò intorno al {reachable:.0f}%.",
            grid_fallback_at, full_at, extra,
        )

    # Phase 1 is CRITICAL only in the sense that it charges regardless of sun; the
    # deadline itself may be days away, so it is judged like any other case below.
    if decision.urgency == URGENCY_CRITICAL and phase1_soc is None:
        detail = (f"Sto già caricando anche da rete per arrivare al "
                  f"{need.target_soc:.0f}% entro {when}. "
                  f"Servono ancora {_durata(hours_needed)}.")
        if full_at:
            detail += (f" Al ritmo attuale è {_target_adj(need.target_soc)} "
                       f"{_quando(full_at, now, 'alle')}.")
        return (OUTLOOK_CHARGING_GRID, detail, grid_fallback_at, full_at, extra)

    outlook = OUTLOOK_ON_TIME
    if grid_fallback_at - now < timedelta(hours=1):
        outlook = OUTLOOK_GRID_SOON

    if phase1_soc is not None:
        attesa = (f"Adesso carico comunque fino al {phase1_soc:.0f}%, poi aspetto il sole; "
                  f"se non basta, {_quando(grid_fallback_at, now, 'alle')}")
    else:
        attesa = (f"Aspetto il sole, ma se non arriva, "
                  f"{_quando(grid_fallback_at, now, 'alle')}")
    detail = (
        f"Sì. Entro {when} ho {_durata(hours_available)} e me ne servono "
        f"{_durata(hours_needed)}. {attesa} attacco alla rete e l'auto è comunque "
        f"{_target_adj(need.target_soc)} in tempo."
    )
    if full_at:
        detail += (f" Al ritmo attuale sarebbe {_target_adj(need.target_soc)} "
                   f"{_quando(full_at, now, 'alle')}.")
    return (outlook, detail, grid_fallback_at, full_at, extra)


# ── Entry point ──────────────────────────────────────────────────────────────

def build_status(
    now: datetime,
    decision,
    signals,
    need,
    cfg,
    state,
    mode: str,
    charging_enabled: bool,
    target_label: Optional[str] = None,
    reference_current: float = 15.0,
    phase1_soc: Optional[float] = None,
    plan_start_at: Optional[datetime] = None,
) -> ChargeStatus:
    """Assemble the plain-language status from the planner's decision.

    `phase1_soc` is the AUTO phase-1 threshold when that rule, and not the
    deadline, is what is forcing the charge; None otherwise.

    `plan_start_at` is when the deadline plan intends to take the grid, which on
    a two-rate tariff is when the cheap band opens rather than the last moment
    the deadline allows. Without it the status quotes the last moment, and the
    card tells the owner a time the charge will not wait for.
    """
    try:
        headline, detail = _describe_action(
            now, decision, signals, cfg, state, mode, charging_enabled,
            ev_soc=need.ev_soc, phase1_soc=phase1_soc,
        )
        outlook, outlook_detail, grid_at, full_at, extra = _describe_outlook(
            now, decision, signals, need, cfg, target_label, reference_current,
            charging_enabled, phase1_soc=phase1_soc,
            plan_start_at=plan_start_at,
        )
    except Exception as err:  # a status sensor must never break the control loop
        _LOGGER.exception("Could not build status text: %s", err)
        return ChargeStatus(headline="—", detail=str(err))

    # A car that has reached what the deadline asked for has nothing left to do,
    # whatever the planner would command.
    if outlook == OUTLOOK_DONE:
        headline = "Auto carica"
        if need.target_soc < 99.5:
            detail = (f"L'auto è al {need.ev_soc:.0f}%, l'obiettivo di oggi era "
                      f"{need.target_soc:.0f}%: non serve altro.")
        else:
            detail = f"L'auto è al {need.ev_soc:.0f}%: non c'è altro da caricare."

    # While it is standing still, the status line has room for the two questions
    # anyone actually asks of it: when does this start, and does it still make the
    # deadline. Both are already known here; they were just not being said.
    starts_at = None
    deadline_line = ""
    base_detail = detail
    if outlook not in (OUTLOOK_DONE, OUTLOOK_PAUSED) and decision.amps <= 0:
        starts_at = _when_start(now, decision, signals, cfg, state)
        if starts_at:
            detail = f"{_phrase_end(detail)} {starts_at}"
        if need.seconds_to_target is not None:
            target_at = now + timedelta(seconds=need.seconds_to_target)
            when = _quando(target_at, now, "")
            deadline_line = (f"Target {when}, fra "
                             f"{_durata(need.seconds_to_target / 3600)}.")
            if grid_at and grid_at > now:
                deadline_line += (f" Se il sole non basta attacco alla rete "
                                  f"{_quando(grid_at, now, 'alle')}, fra "
                                  f"{_durata((grid_at - now).total_seconds() / 3600)}.")
            elif grid_at:
                deadline_line += " Sono già oltre l'ora del passaggio a rete."
            detail = f"{_phrase_end(detail)} {deadline_line}"

    attrs: Dict[str, Any] = {
        "perche": detail,
        # The same reason without the two appended sentences, for surfaces that
        # show them separately (the Lovelace card) and would otherwise repeat them.
        "perche_breve": base_detail,
        "quando_parto": starts_at or "—",
        "scadenza": deadline_line or "—",
        "ce_la_fa": outlook,
        "dettaglio_previsione": outlook_detail,
        "modalita": mode.upper(),
        "urgenza": decision.urgency,
        "stop_oggi": decision.stops_today,
        "sole": {SUN_SETTING: "sta calando", SUN_DARK: "finito"}.get(
            decision.sun_phase, "ancora alto"),
        "sole_perche": decision.sun_reason,
        "dettaglio_tecnico": decision.reason,
    }
    if grid_at and grid_at > now:
        attrs["al_piu_tardi_rete_dalle"] = _hhmm(grid_at)
    else:
        grid_at = None
    if full_at:
        attrs["piena_alle"] = _hhmm(full_at)
    attrs.update(extra)

    return ChargeStatus(
        headline=headline,
        detail=detail,
        outlook=outlook,
        outlook_detail=outlook_detail,
        grid_fallback_at=grid_at,
        full_at=full_at,
        attributes=attrs,
    )


def legacy_status(mode: str, message: str, charging_enabled: bool) -> ChargeStatus:
    """Status for the modes the planner does not run: NORMAL, POWER, or planner off."""
    if not charging_enabled:
        return ChargeStatus(headline="Ricarica disattivata",
                            detail="L'hai messa in pausa tu.",
                            outlook=OUTLOOK_NO_TARGET)
    labels = {
        "normal": ("Modalità Normal", "Corrente fissa impostata da te, integrata dal sole quando c'è."),
        "power": ("Modalità Power", "Massima potenza disponibile: sole, batteria e rete insieme."),
    }
    head, why = labels.get(mode, (f"Modalità {mode.upper()}", message or ""))
    return ChargeStatus(
        headline=head,
        detail=why,
        outlook=OUTLOOK_NO_TARGET,
        outlook_detail="Modalità manuale: nessuna scadenza da rispettare.",
        attributes={"perche": why, "modalita": mode.upper(),
                    "dettaglio_tecnico": message or ""},
    )


# ── Debug figures ────────────────────────────────────────────────────────────

@dataclass
class ChargeDebug:
    """The four numbers worth watching, computed in every mode.

    These deliberately do not depend on the planner: the planner only runs for the
    solar modes, and "when does this become critical" is exactly the question you
    ask while sitting in NORMAL or POWER wondering whether to leave it alone.
    """

    next_target_at: Optional[datetime] = None      # when the car must be full
    critical_at: Optional[datetime] = None         # when we stop waiting for sun
    charge_hours_at_reference: Optional[float] = None   # at the AUTO current
    hours_to_full_now: Optional[float] = None      # at what the car is drawing now
    full_at: Optional[datetime] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


def build_debug(
    now: datetime,
    ev_soc: Optional[float],
    ev_capacity_kwh: float,
    ev_power_w: float,
    seconds_to_target: Optional[int],
    target_label: Optional[str],
    reference_current_a: float,
    voltage: float,
    critical_margin: float = 1.10,
    target_soc: float = 100.0,
) -> ChargeDebug:
    """Compute the deadline and duration figures shown as diagnostics."""
    dbg = ChargeDebug()
    attrs: Dict[str, Any] = {}

    missing = max(0.0, target_soc - ev_soc) if ev_soc is not None else None
    energy_kwh = None
    if missing is not None and ev_capacity_kwh:
        energy_kwh = ev_capacity_kwh * missing / 100.0 / CHARGING_EFFICIENCY
        attrs["soc_auto"] = round(ev_soc, 1)
        attrs["energia_mancante_kwh"] = round(energy_kwh, 2)

    # How long a full charge takes at the reference current (15 A by default).
    ref_kw = reference_current_a * voltage / 1000.0
    if energy_kwh is not None and ref_kw > 0:
        dbg.charge_hours_at_reference = energy_kwh / ref_kw
        attrs[f"carica_a_{reference_current_a:.0f}A"] = _durata(dbg.charge_hours_at_reference)

    # How long at whatever the car is actually drawing, in any mode.
    if energy_kwh is not None and ev_power_w > 200:
        hours = energy_kwh / (ev_power_w / 1000.0)
        if 0 < hours < 72:
            dbg.hours_to_full_now = hours
            dbg.full_at = now + timedelta(hours=hours)
            attrs["al_ritmo_attuale"] = _durata(hours)
            attrs["piena_alle"] = _hhmm(dbg.full_at)
            attrs["potenza_auto_w"] = round(ev_power_w)

    if seconds_to_target is not None:
        dbg.next_target_at = now + timedelta(seconds=seconds_to_target)
        attrs["prossimo_target"] = target_label or _hhmm(dbg.next_target_at)
        attrs["fra"] = _durata(seconds_to_target / 3600.0)
        if dbg.charge_hours_at_reference is not None:
            # The moment the deadline logic flips to CRITICAL and starts using the
            # grid: target minus the time a full charge needs, plus its margin.
            dbg.critical_at = dbg.next_target_at - timedelta(
                hours=dbg.charge_hours_at_reference * critical_margin
            )
            attrs["critical_alle"] = _hhmm(dbg.critical_at)
            left = (dbg.critical_at - now).total_seconds() / 3600.0
            attrs["critical_fra"] = _durata(left) if left > 0 else "adesso"
    else:
        attrs["prossimo_target"] = "nessuno"

    dbg.attributes = attrs
    return dbg
