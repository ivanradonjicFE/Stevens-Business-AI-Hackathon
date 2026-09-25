How it works

The system is a Python pipeline that watches for global events that could disrupt semiconductor production or shipping, and turns them into auditable alerts. All data comes from free public sources.

Collect: pulls current disasters from GDACS, USGS and NASA EONET, plus 72 hours of global news from GDELT. The news covers supply-chain disruptions, canal and strait closures, wars, and alarmist headlines.
Filter and score: matches each event against 29 critical chip-industry sites (fabs, packaging hubs, ASML, key materials sources, shipping chokepoints) using distance and alert level. It then scores relevance and severity. News is grouped into stories, weighted by how many outlets confirm it, and discounted when headlines are alarmist.
Compare with history: finds the most similar of 19 past chip supply shocks. It calculates each one's actual market impact live from Yahoo Finance: semiconductor index vs S&P 500 at +1, +5 and +20 days. It also pulls each shock's supply and demand effects live from Wikipedia.
Project: combines those precedents, weighted by similarity and adjusted for severity, into an expected market move. It compares that with how chip stocks are moving now, e.g. "reaction may still be ahead" or "already priced in".
Alert: writes a ranked alert (ADVISORY, WATCH or WARNING) as Markdown and PDF, plus a JSON audit trail showing how every score was reached.
Current architecture


collect.py  →  semis.py  →  analogs.py + impacts.py  →  alert.py  →  to_pdf.py
(sources)      (filter/     (market history +          (outlook +    (PDF)
               score)        supply/demand)             alert)
                         run.py orchestrates all steps
Rule-based: every number traces to code and data, with no AI model in the loop, so every result is reproducible.
Resilient: news results are cached for 15 minutes, and if one source fails the rest still run.
Not built yet: continuous scheduling, automatic alert delivery, and the Health and Insurance impact briefs.
