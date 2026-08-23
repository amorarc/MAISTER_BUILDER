"""Measure whether the design brief actually produces different designs.

Every other checker in this project has a number. The brief has not had one,
which means each of the things done to it - the variation angle, the stance, the
verbalized sampling, naming `avoid` first - has been an argument rather than a
result. This is the measurement that settles them.

    python tools/brief_diversity.py "a house" --runs 12
    python tools/brief_diversity.py "a tree" --runs 12 --compare

**It spends API calls**: one per run, per configuration. `--compare` runs four
configurations, so `--runs 12 --compare` is 48 calls. Start small.

# What is measured

The briefs are embedded with the same local Qwen3 encoder the retrieval indexes
use, and two numbers are reported over the resulting vectors:

* **mean pairwise distance** - 1 minus the average cosine similarity between
  every pair. Easy to read, and it says how far apart the answers are on
  average.
* **Vendi score** - the exponential of the Shannon entropy of the eigenvalues of
  the similarity matrix, which is the metric the diversity literature reports.
  It reads as an *effective number of distinct answers*: twelve briefs that are
  all the same score about 1, twelve genuinely different ones score near 12. It
  is the more honest of the two, because mean distance can be held up by one
  outlier while eleven briefs agree.

Only the fields that describe the model are embedded - `reads_as`, `signature`,
`avoid`, the palette - never the stance or the sampling note. Those differ by
construction, and scoring them would be measuring the thermometer.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maister.agent import brief                      # noqa: E402
from maister.agent.config import BRIEF_CANDIDATES    # noqa: E402


def embedded_text(document):
    """The part of a brief that describes the model, as one string.

    Deliberately not `brief.as_text`: that renders the angle, and the angle is
    fixed per project rather than chosen per brief, so including it would score
    a constant.
    """
    if not isinstance(document, dict):
        return ""
    parts = []
    for key in ("reads_as", "signature", "technique", "avoid"):
        if document.get(key):
            parts.append(f"{key}: {document[key]}")
    palette = document.get("palette")
    if isinstance(palette, dict):
        parts.append("palette: " + "; ".join(
            f"{role}: {brief._colour(entry)}"
            for role, entry in palette.items() if entry))
    elif isinstance(palette, list):
        parts.append("palette: " + "; ".join(
            brief._colour(e) for e in palette if e))
    return "\n".join(parts)


def vendi_score(vectors):
    """Effective number of distinct items among L2-normalized ``vectors``.

    exp(entropy of the eigenvalues of K/n), K the cosine similarity matrix. One
    means every item is the same; n means they are mutually orthogonal.
    """
    if len(vectors) < 2:
        return float(len(vectors))
    similarity = vectors @ vectors.T
    values = np.linalg.eigvalsh(similarity / len(vectors))
    values = values[values > 1e-12]
    if values.size == 0:
        return 1.0
    return float(np.exp(-(values * np.log(values)).sum()))


def mean_pairwise_distance(vectors):
    if len(vectors) < 2:
        return 0.0
    similarity = vectors @ vectors.T
    upper = similarity[np.triu_indices(len(vectors), k=1)]
    return float(1.0 - upper.mean())


def collect(subject, runs, candidates, use_stance, use_angle, verbose=True):
    """``runs`` briefs for ``subject``, each from its own call.

    Always run as an INVITED request, whatever the subject reads as. This tool
    measures the diversity machinery - the angle, the stance, the tail - and a
    plain subject like "a house" would otherwise take the mode every time and
    report the machinery as doing nothing, which would be a measurement of the
    licence rather than of what it is switched on for. Real runs decide the
    licence from the request; see ``brief.licence``.
    """
    out = []
    for index in range(runs):
        # A different seed per run is the point: this is asking what the pass
        # does across projects, not what it does when asked the same thing twice
        # with the same seed (which is deterministic on purpose).
        seed = f"diversity:{subject}:{index}"
        document = brief.compose(
            subject,
            angle=brief.variation(seed, brief.INVITED) if use_angle else None,
            stance=brief.persona(seed, brief.INVITED) if use_stance else None,
            seed=seed,
            candidates=candidates,
            allowed=brief.INVITED)
        if document:
            out.append(document)
        if verbose:
            reads = (document or {}).get("reads_as") or "(nothing)"
            print(f"    {index + 1:2}/{runs}  {reads[:88]}", flush=True)
    return out


def score(documents, encoder):
    texts = [t for t in (embedded_text(d) for d in documents) if t.strip()]
    if len(texts) < 2:
        return None
    vectors = encoder.encode(texts)
    return {
        "briefs": len(texts),
        "vendi": vendi_score(vectors),
        "distance": mean_pairwise_distance(vectors),
        # A blunt second opinion that needs no model at all: how many distinct
        # first lines came back. If this is 2 out of 12, no embedding metric is
        # going to rescue the verdict.
        "distinct_silhouettes": len({
            (d.get("reads_as") or "").strip().lower() for d in documents}),
    }


def report(label, result):
    if result is None:
        print(f"  {label:34} - too few briefs came back to score")
        return
    print(f"  {label:34} Vendi {result['vendi']:5.2f} / {result['briefs']:<3}"
          f"  distance {result['distance']:.3f}"
          f"  distinct silhouettes {result['distinct_silhouettes']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject", help='what to brief, e.g. "a house"')
    parser.add_argument("--runs", type=int, default=8,
                        help="briefs per configuration (one API call each)")
    parser.add_argument("--compare", action="store_true",
                        help="measure all four configurations, not just the "
                             "shipped one")
    parser.add_argument("--json", type=Path,
                        help="write the briefs and scores here")
    args = parser.parse_args()

    if args.compare:
        # Each configuration turns off one thing, so the difference between two
        # rows is attributable to that thing rather than to the whole pass.
        configurations = [
            ("direct (1 brief, no cues)", 1, False, False),
            ("+ angle", 1, False, True),
            ("+ angle + stance", 1, True, True),
            (f"+ sampling ({BRIEF_CANDIDATES}) - shipped",
             BRIEF_CANDIDATES, True, True),
        ]
    else:
        configurations = [(f"shipped ({BRIEF_CANDIDATES} candidates)",
                           BRIEF_CANDIDATES, True, True)]

    calls = args.runs * len(configurations)
    print(f"{args.subject!r}: {args.runs} run(s) x {len(configurations)} "
          f"configuration(s) = {calls} API call(s)\n")

    from maister.retrieval.encoder import get_encoder
    encoder = get_encoder()

    results, everything = [], {}
    for label, candidates, stance, angle in configurations:
        print(f"  {label}")
        documents = collect(args.subject, args.runs, candidates, stance, angle)
        everything[label] = documents
        results.append((label, score(documents, encoder)))
        print()

    print(f"{args.subject!r} - higher is more varied\n")
    for label, result in results:
        report(label, result)

    # What the sampling is doing, when it ran: a tail that is always the same
    # rank is a tail that is not being sampled.
    chosen = Counter()
    for documents in everything.values():
        for document in documents:
            probability = (document.get("sampling") or {}).get(
                "chosen_probability")
            if probability is not None:
                chosen[probability] += 1
    if chosen:
        print("\n  probabilities of the briefs chosen: "
              + ", ".join(f"{p}x{n}" for p, n in sorted(chosen.items())))

    if args.json:
        args.json.write_text(json.dumps(
            {"subject": args.subject,
             "scores": {label: result for label, result in results},
             "briefs": everything}, indent=1, default=str), encoding="utf-8")
        print(f"\nwritten to {args.json}")


if __name__ == "__main__":
    main()
