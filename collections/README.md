# Curated author collections

## Cancer researchers

[`cancer_researchers.json`](../src/litletter/data/author_groups/cancer_researchers.json)
is a broad,
discovery-focused watchlist of 205 active and influential cancer researchers,
reviewed on 11 August 2026. It emphasizes researchers likely to produce basic,
translational, computational, or early clinical preprints rather than forming a
lifetime-achievement ranking.

The combined `cancer-watchlist` group includes eight mutually exclusive groups:

| Group | Researchers |
| --- | ---: |
| Cancer genomics and evolution | 23 |
| Cancer immunology and cell therapy | 30 |
| Tumour microenvironment and metastasis | 21 |
| Epigenetics, DNA repair and cell death | 29 |
| Precision oncology and drug resistance | 28 |
| Early detection, computational and spatial biology | 25 |
| Haematological malignancies | 26 |
| Organ-specific translational oncology | 23 |
| **Combined** | **205** |

Selection considered recent cancer-preprint activity, recent scholarly
influence, major cancer awards and funding, leadership of field-defining
programmes, geographic breadth, and coverage of the major research specialties.
Seed sources are recorded in the JSON file and include NCI Outstanding
Investigator recipients, AACR scientific awards, Cancer Grand Challenges,
bioRxiv metadata, and OpenAlex.

This remains a curated judgement, not an objective or exhaustive ranking.
Prominence and research activity change, so the collection should be reviewed
at least annually. Canonical full names and targeted aliases are used for high
recall. Initial-only given names are matched for distinctive researcher names
but disabled for ambiguous names such as Campbell, Chen, Jones, Shi, Wang, and
Wu. bioRxiv and medRxiv's corresponding-author full name provides a safer match
for those exceptions. Institutions are documentation only, so exact common-name
collisions can still occur.

To use the bundled collection, set:

```json
"author_groups": "builtin:cancer_researchers"
```

Then add a preprint category:

```json
{
  "id": "cancer-researcher-watchlist",
  "name": "Cancer Researcher Watchlist",
  "query": "author_group:cancer-watchlist",
  "sources": ["biorxiv", "medrxiv", "arxiv"]
}
```
