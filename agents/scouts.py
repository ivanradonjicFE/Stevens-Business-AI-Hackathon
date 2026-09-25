"""Scout agents: poll sources on their own schedules and drop raw signals on the blackboard."""
import collect
import config
import store
from agents import Agent, to_dict


class HazardScout(Agent):
    """Tropical cyclones and tsunamis: GDACS cyclones, NASA EONET storms, NOAA tsunami warnings, USGS tsunami-flagged quakes."""
    name = "HazardScout"
    every = config.HAZARD_SCOUT_EVERY

    def run(self):
        collect.LOOKBACK_DAYS = config.HAZARD_LOOKBACK_DAYS
        events, counts = [], {}
        for fn in (collect.gdacs, collect.eonet, collect.tsunami_noaa, collect.usgs):
            try:
                got = fn()
                counts[fn.__name__] = len(got)
                events += got
            except Exception as ex:
                counts[fn.__name__] = f"FAILED {type(ex).__name__}"
        events = collect._dedupe(events)
        new = sum(store.upsert_signal(to_dict(e)) for e in events)
        store.db().commit()
        self.mark()
        self.log("scanned", f"{counts} -> {new} new signals")
        return new


class NewsScout(Agent):
    """Global news via GDELT: cyclones/tsunamis + chips, + ports/airports/power, and tsunami warnings."""
    name = "NewsScout"
    every = config.NEWS_SCOUT_EVERY

    def run(self):
        events = collect.gdelt()
        new = sum(store.upsert_signal(to_dict(e)) for e in events)
        store.db().commit()
        self.mark()
        self.log("scanned", f"{len(events)} headlines from {len(collect.NEWS_QUERIES)} searches -> {new} new signals")
        return new
