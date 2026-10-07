# MODULE: hwpx

Byte-level HWPX writing by surgically editing the seed template. Most reproducibility-sensitive module — small byte changes break `make reproduce`.

## FILES
| File | Role |
|------|------|
| `zip_surgery.py` | `unpack`/`repack`/`replace_entry`. `HwpxEntries` preserves per-entry compress_type/external_attr/create_system. |
| `anchor_map.py` | `load_anchor_map` → `AnchorMap` (slot → `NodeRef`). HP/HS namespaces; `SECTION_HEADING_TEXT` (5 headings). Default map: `tests/fixtures/anchor_map.json`. |
| `xml_writer.py` | `set_text` (slot-targeted text in `Contents/section0.xml`), `sync_prv_text` (mirror to `Preview/PrvText.txt`), `clone_paragraph`, `blank_direct_paragraphs`, `section_property_direct_ordinals`, `set_direct_paragraph_char_properties`. |
| `typography.py` | The form's own typesetting rule ("A4 / 돋움 11pt / 줄간격 160%") as four appended `charPr` entries (ids 7-10). `install_form_typography` patches the rendered `Contents/header.xml`; `apply_compact_table_runs` moves table paragraphs to the compact style; `declares_form_typography` lets `image_embed` avoid a dangling caption reference. |
| `table_writer.py` | `write_kpi_table` (index 0), `write_gantt` (index 1) via raw `<hp:tbl>` byte splicing. |
| `table_fit.py` | `fit_tables` — post-write column widths measured from cell content; the seed's equal split becomes a proportional one that spans `BODY_WIDTH`. Row heights untouched. |
| `validate.py` | `validate_public_artifact` (final gate), `text_extract`, `auto_repair` (fixes mimetype-order/compression corruption only → reproducible bytes), `ValidationReport`/`RepairError`. `MAX_PUBLIC_ARTIFACT_CHARS=15_000`. Also runnable as a CLI. |
| `visual_preview.py` | QA-only HWPX→paginated HTML/PDF/PNG renderer. Maps form geometry, typography, tables, page breaks, and figures so agents can inspect every page directly; it is not a Hancom submission renderer. Chrome resolves from `KIMM_DOCBOT_CHROME` or common browser candidates. |

`png_scale.py` downsizes PNGs at an exact integer ratio. Decoding is limited to
the IHDR-declared raster (at most 128 MiB); malformed/truncated streams raise
`ImageEmbedError`. Factor selection enumerates common divisors, not image width.

## HARD RULES (HWPX/ZIP correctness)
- `mimetype` entry MUST be first and `ZIP_STORED` (uncompressed) — `_ordered_for_hwpx` enforces order.
- Reuse the seed's compression/attrs for every entry; unknown entries fall back to seed values. Never recompress blindly.
- `ZIP_EPOCH = (1980,1,1,0,0,0)` fixed timestamp + recomputed CRC → reproducible bytes. Do not use real mtimes.
- Editing section text? Also `sync_prv_text` so the preview matches, or validation/extraction diverges.
- Figures are inline (`treatAsChar="1"`) in their own centred paragraph (paraPr 26, `keepWithNext`), and the caption is the **next plain paragraph** (`그림 N. …`, caption style 21 / paraPr 19), never an `hp:caption`. Both read-backs (`validate.py`, `figure_density.py`) pin this shape - do not reintroduce floating pictures or `hp:caption`. History: floats (2026-08-30, to backfill the gap an inline figure leaves at a page foot) covered text instead — with `flowWithText="1"` a float that does not fit below its anchor is pulled up inside the page over the preceding prose, and without `linesegarray` a viewer reserves no line height for an `hp:caption`, so the caption was drawn over the next sentence (owner report 2026-09-30, reproduced in rhwp on all 6 figures). A gap at a page foot is the accepted cost; covered text is not.
- Table layout assumes KPI table = index 0, Gantt = index 1 in the seed; reindex here if the seed changes (it must not).
- **Declared column widths are ours; declared row heights are Hancom's.** `fit_tables` runs after every table writer and re-splits columns by content demand, because a table cloned from the seed keeps the seed's own proportions no matter what it now holds. Cell heights stay exactly as written — `noAdjust="0"` makes Hangul recompute them, so estimating heights here only adds whitespace it will not take back.
- **`hp:secPr` is the form.** It rides in the FIRST paragraph and holds page size, margins and the outline shape for the whole section. Never splice that paragraph out — `remove_seed_guidance` empties it instead. Losing it silently hands Hangul its own page setup.
- **One definition of "markdown block → form paragraph"**: `figure_density._band_body_paragraphs`, reached through `render_layout_bands` (with figures) or `render_body_paragraphs` (without). The older `render_paragraph_blocks` cloned a neighbouring seed paragraph and inherited its character property; it was deleted (2026-09-18) so it cannot be picked up again — insert body prose only through the two entry points above.
- **Anchor by element path + ordinal index, NEVER by text content** (`anchor_map` / `NodeRef`) — text shifts, paths don't.
- **NEVER full ElementTree/lxml re-serialization** of the document and **never hand-write XML**; edit via targeted splice on the seed bytes (ET is used only to *locate* nodes). **No live image/figure injection.**
- `validate.py` is the single authoritative validator — do NOT duplicate validation logic in other modules; delegate to it.
