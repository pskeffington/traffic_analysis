# Radio traffic patterns in v1

The analyzer starts after TRANS has produced timestamped text segments and `content_kind`. It does not read audio, classify sound, or rewrite transcript text.

## Patterns preserved or extracted

- **Addressing/contact:** `UNIT 12 to DISPATCH`, `DISPATCH, this is UNIT 12`, and `DISPATCH from UNIT 12` produce a directed `RADIO_CONTACT` candidate from the origin identifier to the addressee.
- **Acknowledgment/readback:** `roger`, `copy`, `wilco`, `affirmative`, `negative`, `10-4`, and `received` are message labels only. They do not create edges by themselves.
- **Repeat/correction:** `say again`, `repeat`, `correction`, `scratch that`, and `disregard` are labels; the original and corrected transcript segments remain separate evidence.
- **Hold/wait:** `stand by`, `wait one`, and `hold traffic` are labels only.
- **Status:** `en route`, `on scene`, `arrived`, `in service`, `out of service`, `available`, `unavailable`, `transporting`, and `clear scene` are labels only.
- **Priority traffic:** `emergency traffic` and `urgent traffic` are labels only; the tool does not infer an incident category from them.
- **Audible events:** TRANS-labeled `audible_event` rows are retained with their source and time. They are not parsed as speech or silently discarded.

The patterns are deliberately conservative. An edge means only that a transcript segment explicitly encodes a radio contact. It does not establish identity, affiliation, physical proximity, intent, or a real-world relationship. Acknowledgment words, shared locations, common timing, and similar identifiers do not create edges. Radio identifier keys are scoped to the TRANS `call_id`, not merged across calls. All extracted edges begin in `pending` review.

## Reference basis

The patterns reflect general structural features found across published radio procedures: identify a station and caller, acknowledge receipt, report status, and keep messages clear. FEMA's NIMS guidance favors plain language across incident-management organizations. Local fire dispatch procedures show common patterns such as unit-number acknowledgments, en-route and on-scene reports, and channel changes. These are examples, not a claim that every agency or the Room 237 corpus follows the same terminology.

Ten-codes and other agency-specific shorthand vary by jurisdiction. This version recognizes `10-4` only as an acknowledgment label; it does not map a code to a meaning or use a code as a connection. Add corpus-specific dictionaries only after review against the relevant radio-service procedure.

- [FAA AIM, Radio Communications Phraseology and Techniques](https://www.faa.gov/air_traffic/publications/aim_html/chap4_section_2.html)
- [FAA Order JO 7110.65, Pilot Acknowledgment/Read Back](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap2_section_4.html)
- [FAA Flight Services, Radio Message Format](https://www.faa.gov/air_traffic/publications/atpubs/fs_html/chap4_section_2.html)
- [FEMA, National Incident Management System, Communications Standards and Formats](https://www.fema.gov/sites/default/files/2020-07/fema_nims_doctrine-2017.pdf)
- [Chester County Fire Service, Communications Procedure](https://chestercountysc.gov/forms/1-3%20Communications%20NEW%20CORRECTED.pdf)
- [USFA, Mastering the MAYDAY—local fire radio procedure example](https://apps.usfa.fema.gov/pdf/efop/efo46119.pdf)
