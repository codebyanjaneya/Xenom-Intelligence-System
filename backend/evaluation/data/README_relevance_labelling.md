# Relevance labelling guide

File to fill in: `relevance_labels_review.csv` (118 rows). `relevance_labels.csv` is the untouched original.
Edit only `human_label` and `notes`. Save as CSV (UTF-8). Do not change any other column.

## human_label
| Value | Meaning |
|---|---|
| `accept` | Genuinely relevant to the client named in the `client` column |
| `reject` | Not genuinely relevant to that client |
| `unsure` | Insufficient evidence to decide |
| blank | Not reviewed yet |

Use `notes` for a short reason, especially for `unsure`.

## Rules
- **Do not copy `current_decision` into `human_label`.** `current_decision` is the rule engine's output; the point of this exercise is to compare it against your independent judgement.
- For rows the engine rejected, `client` is a **candidate** (the first client the engine considered), not a verified client. Judge relevance to that named client; if the article clearly matters to a different client, say so in `notes`.
- `unsure` and blank rows are excluded from metrics.

## When finished
From `backend`: `python -m evaluation.cli report --labels evaluation/data/relevance_labels_review.csv`
Metrics are reported as "not measured" until labels exist. Small samples describe the sample only, not overall accuracy.
