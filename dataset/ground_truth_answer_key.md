# Ground Truth Answer Key — SIH 26189 Synthetic Dataset

**INTERNAL USE ONLY. Do not include this file in the demo bundle shown to judges or evaluators.**

This document records exactly what was planted in the dataset so the team can verify that the
extraction, graph-building and pattern-detection modules find *these* patterns, and not
hallucinated substitutes. Every ID below has been checked against the generated files by
`verify.py`.

Data window: **2 March 2026 – 12 April 2026** (6 weeks).
All persons, numbers, plates, addresses, companies and case numbers are fictional.

---

## 1. The three networks

### Network A — "Ashvamedh" investment fraud and layered fund movement (7 members)

Money is collected from members of the public on the promise of commodity-trading returns,
pooled in a front company's current account, then layered outward through low-KYC mule
accounts and drawn in cash.

| person_id | Name | Alias variants planted in documents | Role |
|---|---|---|---|
| P0101 | Devanshu Kalgutkar | D. Kalgutkar, Devanshu R. Kalgutkar, Deva Kalgutkar | Controller / financier |
| P0102 | Ritambhara Nagvekar | R. Nagvekar, Ritu Nagvekar, "Ritu madam" (SR-1001) | Bookkeeper |
| P0103 | Amrapali Deshkulkarni | A. Deshkulkarni, Amrapali D. | Paper director, ORG01 |
| P0104 | Pravin Attarde | P. Attarde, Praveen Attarde | Mule layer 1 |
| P0105 | Suhail Barkatpuri | S. Barkatpuri, Suhel Barkatpuri, "Suhel" (SR-1005) | Mule layer 2 |
| P0106 | Nilesh Wagholikar | N. Wagholikar, Nilesh W. | Cash-out layer 3 |
| P0107 | Jayendra Mokashi | J. Mokashi, "Jayendra" (FIR-2026-0393) | Cash / goods courier |

Organizations: **ORG01 Ashvamedh Trading Solutions Pvt Ltd** (ACC0001), **ORG02 Girija Infra
Consultants** (ACC0008 — the vehicle used to pay the bridge entity).
Locations: Tilakwadi Commercial Complex Unit 12; Vashisht Market; Wagholikar Wada, Purana Peth.
Vehicles: V006 MP09CF8823 (Devanshu, silver Honda City), V007 MP09EG5507 (Jayendra, cargo auto),
V008 MP09AW9014 (Nilesh, white i20), V019, V020.
FIRs: FIR-2026-0388, FIR-2026-0393, FIR-2026-0401, FIR-2026-0428 → **CASE-2026-01**.

### Network B — "Kaveri Depot" smuggling and logistics ring (7 members)

Unaccounted goods consolidated at a cold storage and moved by road on pre-selected nights.

| person_id | Name | Alias variants planted | Role |
|---|---|---|---|
| **P0201** | **Ishaan Talpade** | I. Talpade, Ishan Talapade, Ishaan V. Talpade, "Talpade" (SR-1004) | **BRIDGE ENTITY** — freight broker, sets movement dates |
| P0202 | Bhargav Ronghe | B. Ronghe, Bhargav S. Ronghe | Ring lead, yard operator |
| P0203 | Ruksana Mirajkar | R. Mirajkar, Rukhsana Mirajkar, "Ruksana" (SR-1004) | Warehouse supervisor; registered owner of V001 |
| P0204 | Sameer Tandalekar | S. Tandalekar, Samir Tandalekar | Primary driver |
| P0205 | Tejpal Ghorpadkar | T. Ghorpadkar, Tejpal G. | Second driver |
| P0206 | Vipin Sadhwani | V. Sadhwani, Vipin K. Sadhwani | Downstream receiver / buyer |
| P0207 | Chetan Barguje | C. Barguje, Chetan B. | Lookout on loading nights |

Organizations: **ORG03 Kaveri Depot Logistics Pvt Ltd** (ACC0016), **ORG04 Nandanvan Cold
Storage LLP** (ACC0017), ORG05 Sadhwani Trading Co.
Locations: Nandanvan Cold Storage (Plot 7, Nandanvan Industrial Belt); Kaveri Depot Yard;
Ghatpimpri Toll Plaza; Rewa Gate Check Post.
Vehicles: **V001 MP09TJ4471** (white Tata truck, registered to Ruksana, driven by Sameer),
V002 MP09QR2208 (Bhargav), V003 MP09HK7756, V004 MP09LD3390 (Chetan's bike),
V005 MP09BN1142 (Ishaan's Ertiga), V009 MP09MN6635 (Vipin), V010 MP09PS2274 (Tejpal).
FIRs: FIR-2026-0409, FIR-2026-0417, FIR-2026-0422 → **CASE-2026-02**.

### Network C — noise cluster (6 people, 3 of them deliberate red herrings)

These people generate patterns that superficially resemble criminal signatures. **None of them
should be scored high-risk.** See §7.

| person_id | Name | Surface anomaly | Innocent explanation |
|---|---|---|---|
| P0301 | Meghna Purandarkar | 60–100 calls/week, all between 01:00–04:00 | Night-shift BPO team lead |
| P0302 | Aslam Chinchvadkar | Three cash deposits: ₹9.2L, ₹8.7L, ₹9.5L in 10 days | Agricultural land sale, deed refs in remarks |
| P0303 | Harsh Vadnere | Address identical to a Network B member's | Shared 3-tenant tenancy |
| P0304 | Nutan Belsare | Frequent small supplier payments | Boutique owner |
| P0305 | Farid Dongarkar | Repeated mid-size cash deposits | Scrap dealer, cash trade |
| P0306 | Sarika Ambekar | — | Pure background; schoolteacher |

Background population: **P0401–P0455** (55 persons) with ordinary salary credits, utility
payments and low-frequency calls. Total persons in dataset: **75**.

---

## 2. The bridge entity

**P0201 — Ishaan Talpade** (`+91 90000 14417`, burner `+91 90000 14420` registered in Amrapali
Deshkulkarni's name).

He is the *only* person with edges into both networks. Remove his node and the A and B
components have **no path between them at all**. Expect a large betweenness-centrality spike on
this node and a much smaller one on P0101 (Devanshu) and P0202 (Bhargav), who are hubs within
their own components only.

Evidence of his dual membership, spread across four different files:

- **Money in from A:** TXN from ACC0008 (Girija Infra Consultants, controlled by P0101) → ACC0009
  (Ishaan), ₹3,75,000 on 2026-03-10 and again on 2026-04-09, remark "Logistics advisory retainer".
- **Calls with A:** 7 calls with Devanshu (CALL-000067, 000129, **000218**, 000220, 000381,
  000514, 000668) plus 3 with Ritambhara; 2 burner-to-burner calls
  (`+919000011701` ↔ `+919000014420`).
- **Operational role in B:** named in FIR-2026-0409 as the source of the instruction phone number
  `9000014417`; observed at Nandanvan Cold Storage in SR-1004; the 47-call spike with the driver.
- **Free-text corroboration:** INT-2026-052 hedges toward exactly this conclusion ("low to
  moderate confidence") — useful for testing whether the assistant reports the linkage with
  appropriate uncertainty rather than asserting it as fact.
- **Trap:** his cover is ORG02 Girija Infra Consultants, registered at *his own residence*. FIR-2026-0428
  is the complaint from the unpaid stationery supplier that exposes it.

---

## 3. Pattern 1 — rapid financial pass-through chain

Total elapsed **47.2 hours**, well inside the 48-hour window. Follow `account_id` in
`transactions.csv`:

| # | transaction_id | From | To | Amount (₹) | Timestamp | Cut taken |
|---|---|---|---|---|---|---|
| 1 | **TXN-0133** | ACC0001 (ORG01 Ashvamedh, held by P0103) | ACC0004 (P0104 Pravin) | 18,50,000 | 2026-03-21 11:40 | — |
| 2 | **TXN-0136** | ACC0004 | ACC0005 (P0105 Suhail) | 18,05,000 | 2026-03-22 09:15 | 2.43% |
| 3 | **TXN-0141** | ACC0005 | ACC0006 (P0106 Nilesh) | 17,58,000 | 2026-03-23 07:25 | 2.60% |
| 4 | **TXN-0142** | ACC0006 | EXTERNAL-CASH | 17,50,000 | 2026-03-23 10:52 | 0.46% (cash-out) |

ACC0004 and ACC0005 both carry `kyc_status = MINIMAL-KYC` and were opened in January 2026 —
a secondary signal the risk model should pick up.

**A second, smaller echo of the same chain** is planted so the demo can show pattern *recurrence*
rather than a one-off: **TXN-0180** (₹6,40,000, 02 Apr 10:10) → **TXN-0182** (₹6,24,000, 02 Apr
18:40) → **TXN-0186** (₹6,08,000, 03 Apr 08:05), same three accounts, ~2.5% cuts, 21.9 hours.

Corroboration in free text: **FIR-2026-0401** (bank clerk reports the ₹17,50,000 cash demand),
**SR-1005** (Suhail hands a cloth bag to Nilesh on 22 March), **INT-2026-041** (describes the
layering without naming anyone).

---

## 4. Pattern 2 — communication spike

Pair: **`+91 90000 14417` (P0201 Ishaan Talpade)** ↔ **`+91 90000 14892` (P0204 Sameer Tandalekar)**.

- **Baseline**, 2–16 March: 6 contacts total, never more than 1 per day.
- **Spike**, **17–18 March 2026**: **59 contacts** (47 voice + 12 SMS) — 31 on the 17th, 28 on the 18th.
- **Trigger event**: the consignment run on the night of **19 March**, and the Rewa Gate seizure
  at 00:14 on **20 March** (FIR-2026-0409).
- **Post-event collapse**: 4 contacts on the 19th, then back to ≤1/day for the rest of the window.

A detector keyed on "z-score of daily contact count per number-pair" should surface this pair and
essentially nothing else. Note that P0301 Meghna's raw call volume is *higher* than this pair's —
a volume-only detector will wrongly rank her first. That is intentional (see §7).

Weaker secondary spikes exist around the 28 March and 7 April runs; these are deliberately
sub-threshold and are a bonus, not a requirement.

---

## 5. Pattern 3 — geographic co-occurrence

**Nandanvan Cold Storage, 19 March 2026, 21:08 – 21:44 hrs.** Three independent source types
converge; **no single file contains all the participants**.

| Source file | Evidence | Places whom |
|---|---|---|
| `surveillance_reports.txt` | **SR-1004** (PSI M. Talekar), observation 21:11–21:51 | P0202 Bhargav Ronghe (named), P0201 Ishaan Talpade (as "Talpade"), P0203 Ruksana (as "Ruksana"), P0207 (unnamed man on black motorcycle) |
| `cdr.csv` | Calls on towers **CT-014** (Nandanvan Industrial Belt) and **CT-015** (Cold Storage Gate) between 21:08 and 21:44 | P0203 Ruksana at 21:12:03 and 21:31:47 (CT-014); P0202 Bhargav at 21:22:09 (CT-015); P0207 Chetan at 21:38:55 (CT-015) |
| `vehicle_movement_logs.csv` | Camera **CAM-07** (Nandanvan Cold Storage Gate) and **CAM-08** | V001 MP09TJ4471 in at 21:08, out at 21:44; V005 MP09BN1142 (Ishaan's Ertiga) in at 21:11, out at 21:51; V004 MP09LD3390 (Chetan's bike) at 20:58 |

Cell towers CT-014 and CT-015 resolve via `cell_towers.csv` to coordinates within the Nandanvan
area; CAM-07/CAM-08 resolve to the same gate.

Expected system output: a co-location event binding **P0201, P0202, P0203, P0207** (and, via the
truck, P0204 who drives it) inside a 36-minute window. The join that makes Ishaan visible here is
`vehicles.owner → V005 → CAM-07 timestamp`, which is *only* available from the ANPR file; SR-1004
gives him only as a surname, and he makes no call from that tower.

**Repeat instances** (for showing the pattern recurs): 28 March ~21:19–23:12 and 7 April
~20:58–21:44, same cameras and towers.

---

## 6. Pattern 4 — hidden multi-hop path

Five hops, four different source files, invisible in any one of them:

| Hop | Relationship | Evidence | File |
|---|---|---|---|
| 1 | **P0101 Devanshu Kalgutkar** → calls → **P0201 Ishaan Talpade** | **CALL-000218**, 2026-03-14 16:28, 487 s (`+919000011012` → `+919000014417`) | `cdr.csv` |
| 2 | **P0201 Ishaan** → transfers ₹2,40,000 → **P0203 Ruksana Mirajkar** | **TXN-0101**, 2026-03-16 15:22, ACC0009 → ACC0011, remark "Consultancy - warehouse audit" | `transactions.csv` |
| 3 | **P0203 Ruksana** → owns → vehicle **V001 / MP09TJ4471**, registered at *Flat 3B, Shrivardhan Apartments, Nandanvan* | vehicle row V001 | `vehicles.csv` |
| 4 | that address → is also the address of → **P0205 Tejpal Ghorpadkar** | persons row P0205 | `persons.csv` |
| 5 | **P0205 Tejpal** → named alongside → **P0206 Vipin Sadhwani** | **FIR-2026-0417** (`named_persons` contains both; narrative names them as "Tejpal G." and "Vipin K. Sadhwani") | `firs.json` |

Note the entity-resolution work required: hop 5 only resolves if the NLP module maps the prose
forms *"Tejpal G."* and *"Vipin K. Sadhwani"* to P0205 and P0206. Hop 3→4 only resolves if the
address string is normalised — `vehicles.registered_address` and `persons.address` are written
identically here on purpose, but the same address also appears with the P0303 red herring, which
is the trap described next.

**Expected shortest path P0101 → P0206 is 5 hops.** If the system returns a 3-hop path, check
whether it has wrongly merged Ishaan's two phone numbers with someone else's, or wrongly linked
the two networks through the shared address.

---

## 7. Red herrings — must NOT be flagged high-risk

### RH-1 · P0301 Meghna Purandarkar (`+91 90000 16201`)
Roughly 3–6 calls every night between 01:00 and 04:00 for the entire six weeks — the **highest
raw nocturnal call volume in the dataset**, higher than any Network B member. Why it's innocent:
the called parties are a **rotating pool of 14 different background numbers**, never a fixed pair,
so pair-level burst detection finds nothing. Her salary credits (night-shift allowance) and rent
payment are in `transactions.csv`. She lives at Sanmitra Residency (tower CT-004), nowhere near
either network's locations. INT-2026-055 clears her explicitly.
*Failure mode to watch for:* a detector that ranks on "count of calls between 00:00 and 05:00"
will put her at the top. The correct feature is per-pair burst, not per-person volume.

### RH-2 · P0302 Aslam Chinchvadkar (ACC0019)
Three cash deposits — **₹9,20,000 (06 Mar)**, **₹8,70,000 (11 Mar)**, **₹9,50,000 (16 Mar)** — each
just under the ₹10 lakh reporting line, which reads exactly like structuring. Why it's innocent:
every deposit remark carries a registered sale-deed reference (`GBS/2026/318`, `319`, `320`) for
land parcels at Ganjbasoda survey 44, and he pays conveyancing fees to a lawyer on 18 March. He
has **no calls with any Network A or B number** and no vehicle, address or FIR link to either.
INT-2026-055 clears him.
*Failure mode to watch for:* an amount-threshold rule with no remark/context reading.

### RH-3 · P0303 Harsh Vadnere (`+91 90000 16340`, V011 MP09ZK4408)
His registered address, **Flat 3B, Shrivardhan Apartments, Nandanvan**, is *identical* to that of
P0205 Tejpal Ghorpadkar (Network B) and is also the registered address of truck V001. This is the
single most dangerous trap in the dataset: naive address-based entity linking will pull him into
Network B and may even shorten the Pattern-4 path. Why it's innocent: a **shared three-tenant
tenancy** — established by SR-1009 (society secretary, watchman, four-year tenure) and cleared by
INT-2026-055. He has **zero call contact with any Network A or B number** (asserted by `verify.py`),
his ANPR pattern is a rigid 09:xx-out / 19:xx-in commute on CAM-14 only, and his transactions are
rent and electricity bills. He appears in **FIR-2026-0396** merely as a witness who broke up a
parking quarrel — an FIR co-mention that must not be treated as criminal association.
*Failure mode to watch for:* address equality treated as a hard identity/association edge, and
`named_persons` in an FIR treated as "suspects" rather than "persons mentioned".

### Secondary noise (should sit mid-to-low risk, not zero)
- **P0305 Farid Dongarkar** — recurring cash deposits from scrap trading; plausible but unexplained,
  so a *low* risk score is acceptable, a high one is not.
- **P0304 Nutan Belsare** — dense small-value payment graph from her boutique; a degree-centrality
  metric that ignores edge value may over-rank her.
- **FIR-2026-0371, FIR-2026-0379, FIR-2026-0396, FIR-2026-0433** (CASE-2026-03) are unrelated local
  offences. They exist so the FIR corpus isn't 100% network-relevant. The entity extractor should
  still pull persons, vehicles and locations from them correctly — it just shouldn't connect them
  to CASE-2026-01 or CASE-2026-02.

---

## 8. Entity-resolution challenges deliberately planted

1. **Alias variants** — every key person appears under 2–3 written forms across FIRs, surveillance
   reports and intelligence briefs (see the tables in §1). `persons.aliases` is pipe-delimited and
   is the answer key for this.
2. **Phone numbers written without the country code** — FIR narratives use `9000011012`,
   `9000014417`, `9000011145`, `9000011593` while `phone_numbers.csv` stores `+919000011012` etc.
3. **Fake-ID SIMs** — 6 numbers where `sim_registered_name` ≠ the actual owner's name:
   - `+919000014420` → used by P0201 Ishaan, **registered to Amrapali Deshkulkarni** (a Network A member — this is itself a cross-network link)
   - `+919000011701` → used by P0101 Devanshu, **registered to Chetan Barguje** (a Network B member — second cross-network link)
   - `+919000015240` → used by P0202 Bhargav, registered to an unverified walk-in KYC name
   - `+919000015377` → used by P0206 Vipin, registered to a corporate entity
4. **First-person-only references** — SR-1004 gives Ishaan as "Talpade" and Ruksana as "Ruksana"
   with no surname; SR-1001 gives Ritambhara as "Ritu madam".
5. **Shared address across unrelated persons** — RH-3 above.
6. **Two accounts per controller** — P0101 holds both ACC0002 and ACC0008 (Girija); P0202 holds
   ACC0010 and ACC0016 (Kaveri); P0203 holds ACC0011 and ACC0017 (Nandanvan LLP). Organization
   money and personal money must not be collapsed into one node.
7. **Hedged intelligence language** — INT-2026-047 and INT-2026-052 use "suspected", "unconfirmed",
   "source B, untested", "low to moderate confidence". An assistant that reports these as
   established fact is failing the uncertainty-handling test.

---

## 9. Verification harness

`verify.py` re-checks all of the above against the generated files and exits non-zero on failure.
It asserts: every foreign key resolves; every plate and phone number appearing in an FIR narrative
exists in `vehicles.csv` / `phone_numbers.csv`; the pass-through chain is contiguous, under 48
hours and has per-hop cuts in range; the spike exceeds 40 contacts with a baseline under 5/day;
the co-occurrence window has ≥3 CDR hits and ≥3 ANPR hits plus SR-1004; all five hops of Pattern 4
resolve; and P0303 has no direct contact with any Network B number.

Current status: **0 errors, 0 warnings.**

## 10. File inventory

| File | Rows / items |
|---|---|
| persons.csv | 75 |
| phone_numbers.csv | 87 |
| cdr.csv | 767 |
| cell_towers.csv | 26 |
| transactions.csv | 225 |
| bank_accounts.csv | 87 |
| vehicles.csv | 20 |
| vehicle_movement_logs.csv | 383 |
| firs.json | 11 |
| surveillance_reports.txt | 6 reports |
| intelligence_reports.txt | 4 briefs |
| organizations.csv | 6 |
| case_files.csv | 3 |
