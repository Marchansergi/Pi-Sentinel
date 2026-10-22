from app.domain import IdentityStatus as S, Observation
from app.presence_tracker import EventType, PresenceTracker, TrackerConfig

BOX = (100, 50, 300, 450)
MOVED = (110, 55, 310, 455)
FAR = (900, 50, 1100, 450)


def cfg(**kw):
    base = dict(confirm_seconds=3.0, min_observations=3, min_authorized_observations=2, exit_seconds=10.0, unverified_timeout_seconds=12.0)
    base.update(kw)
    return TrackerConfig(**base)


def unk(box=BOX):
    return Observation(box, 0.9, S.UNKNOWN, None, 0.1)


def auth(box=BOX, name="Ana"):
    return Observation(box, 0.9, S.AUTHORIZED, name, 0.8)


def noface(box=BOX):
    return Observation(box, 0.9)


def bad_face(box=BOX):
    return Observation(box, 0.9, S.UNVERIFIED, None, None, ("borrosa",))


def run(tr, start, seconds, fn, step=1.0):
    """Llama a update() cada `step` s; fn(t) devuelve una Observation (o None → ciclo vacío)."""
    evs, t = [], start
    while t <= start + seconds + 1e-9:
        o = fn(t) if fn else None
        evs += tr.update(t, [o] if o is not None else [])
        t += step
    return evs


def confirmed(evs, status=None):
    return [e for e in evs if e.type is EventType.CONFIRMED and (status is None or e.status is status)]


def test_unknown_presence_is_confirmed_once_after_time_and_observations():
    tr = PresenceTracker(cfg())
    ev = []
    for t in (0, 1, 2):
        ev += tr.update(t, [unk()])
    assert confirmed(ev) == []  # 3 observaciones pero aún no han pasado 3 s
    ev += tr.update(3, [unk()])
    c = confirmed(ev, S.UNKNOWN)
    assert len(c) == 1 and c[0].event_id


def test_no_repeated_events_during_same_presence():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 60, lambda t: unk(MOVED if int(t) % 2 else BOX))
    assert len(confirmed(ev)) == 1  # una sola alerta durante 60 s de presencia continua


def test_fleeting_detection_never_becomes_an_event():
    tr = PresenceTracker(cfg())
    ev = tr.update(0, [unk()]) + tr.update(1, [unk()])
    ev += run(tr, 2, 30, None)  # desaparece antes de confirmar
    assert ev == []
    assert tr.snapshot() == []


def test_exit_and_reentry_creates_new_event():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 5, lambda t: unk())
    assert len(confirmed(ev)) == 1
    first = confirmed(ev)[0]
    ev2 = run(tr, 6, 12, None)  # ausente más de exit_seconds
    ended = [e for e in ev2 if e.type is EventType.ENDED]
    assert len(ended) == 1 and ended[0].event_id == first.event_id
    ev3 = run(tr, 30, 5, lambda t: unk())
    c = confirmed(ev3, S.UNKNOWN)
    assert len(c) == 1 and c[0].event_id != first.event_id


def test_short_disappearance_does_not_count_as_exit():
    tr = PresenceTracker(cfg(exit_seconds=10))
    ev = run(tr, 0, 5, lambda t: unk())
    ev += run(tr, 6, 4, None)  # 4 s sin verlo (< exit)
    ev += run(tr, 11, 5, lambda t: unk(MOVED))
    assert len(confirmed(ev)) == 1
    assert not [e for e in ev if e.type is EventType.ENDED]


def test_authorized_person_is_not_unknown():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 20, lambda t: auth())
    assert [e.status for e in confirmed(ev)] == [S.AUTHORIZED]
    assert confirmed(ev)[0].name == "Ana"


def test_occasional_false_unknown_does_not_alarm_the_owner():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 30, lambda t: unk() if int(t) % 5 == 4 else auth())
    assert confirmed(ev, S.UNKNOWN) == []


def test_authorized_and_unknown_simultaneously_alert_for_the_unknown():
    tr = PresenceTracker(cfg())
    ev = []
    for t in range(0, 8):
        ev += tr.update(t, [auth(BOX), unk(FAR)])
    assert {e.status for e in confirmed(ev)} == {S.AUTHORIZED, S.UNKNOWN}
    assert len(confirmed(ev, S.UNKNOWN)) == 1


def test_person_without_visible_face_becomes_unverified_not_unknown():
    tr = PresenceTracker(cfg(unverified_timeout_seconds=12))
    ev = run(tr, 0, 11, lambda t: noface())
    assert confirmed(ev) == []  # todavía dentro del margen para ver la cara
    ev += tr.update(12, [noface()])
    assert [e.status for e in confirmed(ev)] == [S.UNVERIFIED]


def test_low_quality_faces_stay_unverified():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 15, lambda t: bad_face())
    assert [e.status for e in confirmed(ev)] == [S.UNVERIFIED]


def test_unverified_upgrades_to_unknown_when_face_becomes_visible():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 13, lambda t: noface())
    assert [e.status for e in confirmed(ev)] == [S.UNVERIFIED]
    ev2 = run(tr, 14, 5, lambda t: unk())
    assert [e.status for e in confirmed(ev2)] == [S.UNKNOWN]
    assert any(e.type is EventType.ENDED and e.status is S.UNVERIFIED for e in ev2)  # el anterior se cierra


def test_unknown_reclassified_only_with_strong_authorized_evidence():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 5, lambda t: unk())
    assert confirmed(ev, S.UNKNOWN)
    one = tr.update(6, [auth()])
    assert [e for e in one if e.type is EventType.RECLASSIFIED] == []  # un acierto aislado no retira nada
    ev2 = run(tr, 7, 14, lambda t: auth())
    assert any(e.type is EventType.RECLASSIFIED and e.status is S.AUTHORIZED for e in ev2)


def test_authorized_then_unknown_takes_over_same_spot():
    tr = PresenceTracker(cfg())
    ev = run(tr, 0, 6, lambda t: auth())
    ev2 = run(tr, 7, 25, lambda t: unk())  # otra persona ocupa el sitio
    assert any(e.status is S.UNKNOWN for e in confirmed(ev2))


def test_two_unknowns_make_two_events():
    tr = PresenceTracker(cfg())
    ev = []
    for t in range(0, 6):
        ev += tr.update(t, [unk(BOX), unk(FAR)])
    assert len(confirmed(ev, S.UNKNOWN)) == 2


def test_empty_updates_are_safe():
    tr = PresenceTracker(cfg())
    assert tr.update(0, []) == []
    tr.reset()
    assert tr.snapshot() == []


def test_face_only_observation_matches_person_track_by_containment():
    tr = PresenceTracker(cfg())
    ev = []
    for t in range(0, 6):
        # persona + cara fuera de la caja de persona en algunos ciclos
        ev += tr.update(t, [Observation(BOX, 0.9, S.UNKNOWN, None, 0.1)] if t % 2 == 0
                        else [Observation((150, 80, 250, 200), 0.9, S.UNKNOWN, None, 0.1)])
    assert len(confirmed(ev)) == 1
