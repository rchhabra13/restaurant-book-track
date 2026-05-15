"""Test nl_parser.parse on a wide variety of intent phrasings."""

from tests._runner import Result, run_test


def run():
    from nl_parser import (
        parse,
        INTENT_ADD, INTENT_WATCH, INTENT_CHECK, INTENT_REMOVE,
        INTENT_UNWATCH, INTENT_LIST, INTENT_WATCHES, INTENT_STATUS,
        INTENT_HELP, INTENT_PAUSE, INTENT_RESUME, INTENT_UNKNOWN,
    )

    # (input, expected_kind, expected_name, expected_party, expected_date)
    cases = [
        ("add bungalow",                    INTENT_ADD,     "bungalow", 2, ""),
        ("add bungalow to the list",        INTENT_ADD,     "bungalow", 2, ""),
        ("add carbone to my list",          INTENT_ADD,     "carbone",  2, ""),
        ("track ishq",                      INTENT_WATCH,   "ishq",     2, ""),  # "track" maps to watch
        ("watch ishq any",                  INTENT_WATCH,   "ishq",     2, "any"),
        ("watch carbone any 4",             INTENT_WATCH,   "carbone",  4, "any"),
        ("watch ishq on 2026-06-15 for 2",  INTENT_WATCH,   "ishq",     2, "2026-06-15"),
        ("track Carbone on 2026-06-15 for 4", INTENT_WATCH, "Carbone",  4, "2026-06-15"),
        ("check carbone",                   INTENT_CHECK,   "carbone",  2, ""),
        ("is there anything at semma",      INTENT_CHECK,   "semma",    2, ""),
        ("remove tatiana",                  INTENT_REMOVE,  "tatiana",  2, ""),
        ("delete bungalow",                 INTENT_REMOVE,  "bungalow", 2, ""),
        ("stop watching odo",               INTENT_UNWATCH, "odo",      2, ""),
        ("unwatch carbone",                 INTENT_UNWATCH, "carbone",  2, ""),
        ("pause bungalow",                  INTENT_PAUSE,   "bungalow", 2, ""),
        ("resume carbone",                  INTENT_RESUME,  "carbone",  2, ""),
        ("list",                            INTENT_LIST,    "",         2, ""),
        ("my restaurants",                  INTENT_LIST,    "",         2, ""),
        ("watches",                         INTENT_WATCHES, "",         2, ""),
        ("what am I watching",              INTENT_WATCHES, "",         2, ""),
        ("status",                          INTENT_STATUS,  "",         2, ""),
        ("dashboard",                       INTENT_STATUS,  "",         2, ""),
        ("help",                            INTENT_HELP,    "",         2, ""),
        ("what can you do",                 INTENT_HELP,    "",         2, ""),
        ("gibberish completely random",     INTENT_UNKNOWN, "",         2, ""),
        ("https://resy.com/cities/ny/x",    INTENT_ADD,     "",         2, ""),
    ]

    results = []
    for inp, kind, name, party, date in cases:
        def _t(inp=inp, kind=kind, name=name, party=party, date=date):
            i = parse(inp)
            assert i.kind == kind, f"kind: expected {kind!r} got {i.kind!r}"
            if name:
                assert i.name == name, f"name: expected {name!r} got {i.name!r}"
            if party != 2:
                assert i.party_size == party, f"party: expected {party} got {i.party_size}"
            if date:
                assert i.date == date, f"date: expected {date!r} got {i.date!r}"
        results.append(run_test(f"parse({inp!r})", _t))

    return results
