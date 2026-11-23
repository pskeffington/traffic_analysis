# Radio traffic patterns in v1

The analyzer starts after TRANS has produced timestamped text segments and `content_kind`. It does not read audio, classify sound, or rewrite transcript text.

## Patterns preserved or extracted

- **Addressing/contact:** `UNIT 12 to DISPATCH`, `DISPATCH, this is UNIT 12`, and `DISPATCH from UNIT 12` produce a directed `RADIO_CONTACT` candidate from the origin identifier to the addressee.
- **Acknowledgment/readback:** `roger`, `copy`, `wilco`, `affirmative`, `negative`, `10-4`, and `received` are message labels only. They do not create edges by themselves.
- **Repeat/correction:** `say again`, `repeat`, `correction`, `scratch that`, and `disregard` are labels; the original and corrected transcript segments remain separate evidence.
- **Hold/wait:** `stand by`, `wait one`, and `hold traffic` are labels only.
- **Status:** `en route`, `on scene`, `arrived`, `in service`, `out of service`, `available`, `unavailable`, `transporting`, and `clear scene` are labels only.
- **Audible events:** TRANS-labeled `audible_event` rows are retained with their source and time. They are not parsed as speech or silently discarded.

The patterns are deliberately conservative. An edge means only that a transcript segment explicitly encodes a radio contact. It does not establish identity, affiliation, physical proximity, intent, or a real-world relationship. Acknowledgment words, shared locations, common timing, and similar identifiers do not create edges. All extracted edges begin in `pending` review.

## Reference basis

The patterns reflect general structural features in published radiotelephony procedures: call-up, reply, message, acknowledgment; identifying the called station and caller; and repeating identifiers in readbacks. These sources describe aviation procedure and are references for message structure, not a claim that the Room 237 corpus follows aviation phraseology.

- [FAA AIM, Radio Communications Phraseology and Techniques](https://www.faa.gov/air_traffic/publications/aim_html/chap4_section_2.html)
- [FAA Order JO 7110.65, Pilot Acknowledgment/Read Back](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap2_section_4.html)
- [FAA Flight Services, Radio Message Format](https://www.faa.gov/air_traffic/publications/atpubs/fs_html/chap4_section_2.html)
