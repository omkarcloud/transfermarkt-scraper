"""Static lookup endpoints backed by the vendored tmapi attribute tables, so
API users can turn a country / position into the ids the filters take."""
from . import refs


def countries():
    rows = [{"id": c["id"], "name": c["name"], "code": c.get("code"),
             "confederation": refs.confederation(c.get("confederation_id"))}
            for c in refs.attributes()["countries"] if not c.get("is_historical")]
    rows.sort(key=lambda c: c["name"])
    return {"count": len(rows), "countries": rows}


def positions():
    rows = [refs.position(p["id"]) for p in refs.attributes()["positions"]]
    groups = [{"value": k, "name": v} for k, v in refs.POSITION_GROUPS.items()]
    return {"count": len(rows), "positions": rows, "position_groups": groups,
            "age_groups": refs.AGE_GROUPS}
