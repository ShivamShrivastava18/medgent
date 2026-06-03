# Discharge Summary Draft

_Generated 2026-06-03T11:03:42.767114+00:00_  ·  source: `data/synthetic/synth_05/source.pdf`

**Agent metrics:** 43 iterations, 26 tool calls

**Safety flags:** 1

## Patient demographics
- **name:** synth_05
- **age:** 65
- **sex:** M
- **mrn:** 000-00-0005

_Sources: p.1_

## Admission date
2026-07-14

_Sources: p.1_

## Discharge date
2026-07-16

_Sources: p.4_

## Principal diagnosis
Acute exacerbation of COPD with type 2 respiratory failure

_Sources: p.1_

## Secondary diagnoses
- {'diagnosis': 'Hypertension', 'icd_10': 'I10'}
- {'diagnosis': 'Type 2 diabetes mellitus', 'icd_10': 'E11.9'}
- {'diagnosis': 'Atrial fibrillation', 'icd_10': 'I48.91'}
- {'diagnosis': 'Ischemic heart disease', 'icd_10': 'I25.9'}

_Sources: p.1_

## Hospital course
Hospital course summary: This 68-year-old male was admitted on 2026-07-14 for an acute exacerbation of COPD, presenting with worsening shortness of breath, cough, and increased sputum production.

_Sources: p.7_

## Procedures
- {'procedure_name': 'Chest X-Ray (PA view)', 'date': '2026-07-14', 'icd_10_pcs': None, 'notes': 'Investigation performed on admission.'}
- {'procedure_name': 'Sputum for Gram Stain, Culture & Sensitivity', 'date': '2026-07-14', 'icd_10_pcs': None, 'notes': 'Investigation performed on admission.'}
- {'procedure_name': 'ECG', 'date': '2026-07-14', 'icd_10_pcs': None, 'notes': 'Investigation performed on admission.'}

_Sources: p.2, p.7_

## Admission medications

_Sources: p.1_

## Discharge medications
- name_as_written=Augmentin, dose=625mg, route=Oral, frequency=Twice a day, duration=5 days
- name_as_written=Pan D, route=Oral, frequency=Once a day (before breakfast)
- name_as_written=Dolo, dose=650mg, route=Oral, frequency=As needed (SOS) for fever/pain
- name_as_written=Grilinctus Syrup, dose=2 teaspoons, route=Oral, frequency=Three times a day (TDS)
- name_as_written=Duolin and Budocort respules, route=Nebulization, frequency=Alternately every 4 hours
- name_as_written=Deriphyllin Retard, dose=300mg, route=Oral, frequency=Twice a day
- name_as_written=Wysolone, dose=20mg, route=Oral, frequency=Once a day for 5 days, then taper
- name_as_written=LMWH (Low Molecular Weight Heparin), dose=40mg, route=Subcutaneous, frequency=Once a day, duration=5 days

_Sources: p.6_

## Medication changes

_Sources: p.6, p.1_

## Drug interactions

_Sources: p.6_

## Allergies
- {'status': 'No known drug allergies'}

_Sources: p.1_

## Follow-up instructions
['Follow up with Dr. synth_05_doc_1 in 5 days']

_Sources: p.7_

## Pending results  —  **FLAGGED**
> agent attempted FILLED but citations invalid (citation excerpt does not overlap page 1 content); downgraded to FLAGGED by guardrail

## Discharge condition
Patient is being discharged in a stable condition.

_Sources: p.7_

## Safety flags raised
- **[medium]** `pending_results` — agent attempted FILLED but citations invalid (citation excerpt does not overlap page 1 content); downgraded to FLAGGED by guardrail


---
*This is a DRAFT for clinician review. Sections marked PENDING, MISSING, FLAGGED, or CONFLICTING require explicit clinician action before finalizing.*