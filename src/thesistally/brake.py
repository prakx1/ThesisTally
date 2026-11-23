"""Funding decisions at the portfolio close, for orders at the following open."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS drawdown_brake (
    run TEXT PRIMARY KEY REFERENCES runs(id), day TEXT NOT NULL, state TEXT NOT NULL,
    gate_passes INTEGER NOT NULL, at_pause_threshold INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_events (
    run TEXT NOT NULL REFERENCES runs(id), day TEXT NOT NULL, revision TEXT NOT NULL,
    kind TEXT NOT NULL, previous_state TEXT NOT NULL, state TEXT NOT NULL,
    gate_passes INTEGER NOT NULL, equity REAL NOT NULL, peak REAL NOT NULL,
    drawdown REAL NOT NULL, risk_fraction REAL NOT NULL, PRIMARY KEY(run, day)
);
"""


def funding(db, run, day, previous_day, gate, equity, previous_peak, cfg):
    risk = cfg["risk"]
    peak = max(equity, previous_peak)
    dd = 1 - equity / peak
    if "resume_regime_sessions" not in risk:
        # Preserve the exact historical revision, including its float comparisons.
        half = dd >= risk["half_risk_drawdown"]
        return dd >= risk["pause_drawdown"], risk["fraction"] * (0.5 if half else 1)

    prior = db.execute(
        "SELECT * FROM drawdown_brake WHERE run=? AND day=?", (run, previous_day)
    ).fetchone()
    old = prior["state"] if prior else "normal"
    state = old
    at_pause = equity <= peak * (1 - risk["pause_drawdown"])
    passes = 0
    kind = None
    if old == "paused":
        # The session that triggered the pause starts the clock at zero.
        passes = prior["gate_passes"] + 1 if gate else 0
        if passes >= risk["resume_regime_sessions"]:
            state, kind = "recovering", "drawdown_resume"
    elif at_pause and (old == "normal" or not prior["at_pause_threshold"]):
        # At half risk, only a fresh downward crossing can trigger a new pause.
        state, kind = "paused", "drawdown_pause"
    elif old == "recovering" and equity > previous_peak:
        state, kind = "normal", "full_risk_restored"

    half = state != "normal" or equity <= peak * (1 - risk["half_risk_drawdown"])
    fraction = risk["fraction"] * (0.5 if half else 1)
    db.execute(
        """INSERT INTO drawdown_brake(run,day,state,gate_passes,at_pause_threshold)
           VALUES(?,?,?,?,?) ON CONFLICT(run) DO UPDATE SET day=excluded.day,
           state=excluded.state,gate_passes=excluded.gate_passes,
           at_pause_threshold=excluded.at_pause_threshold""",
        (run, day, state, passes, at_pause),
    )
    if kind:
        db.execute(
            """INSERT INTO account_events
               (run,day,revision,kind,previous_state,state,gate_passes,equity,peak,drawdown,risk_fraction)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run,
                day,
                cfg["revision"],
                kind,
                old,
                state,
                passes,
                equity,
                peak,
                dd,
                0.0 if state == "paused" else fraction,
            ),
        )
    # Paused calls use the usual hypothetical size in the shadow book.
    return state == "paused", fraction
