# LLM-Based Call Categorization

## Problem
- Current: Mix of LLM + if/else overrides
- Client doing post-processing = values not accurate
- Rule-based can't distinguish nuanced cases

## Solution
Let LLM fully decide `esito_chiamata` + `motivazione` with strict enum validation.

## Valid Values

### esito_chiamata
- `COMPLETATA`
- `TRASFERITA`
- `NON COMPLETATA`

### motivazione (per esito)
**COMPLETATA:**
- `Info fornite`
- `Pren. effettuata`

**TRASFERITA:**
- `Mancata comprensione`
- `Argomento sconosciuto`
- `Richiesta paziente`
- `Prenotazione`

**NON COMPLETATA:**
- `Interrotta dal paziente`
- `Fuori orario`
- `Problema Tecnico`

## Changes

### 1. Update LLM prompt (lines 256-277)
- Add all examples from client image
- Strict enum values in JSON schema
- Few-shot examples for accuracy

### 2. Add validation function
- Validate LLM returns only valid values
- Map invalid to closest valid
- Log validation failures

### 3. Remove if/else overrides (lines 525-541)
- Trust LLM output for all cases
- Keep only for LLM API failures

### 4. Update analyze_for_transfer
- Use same enhanced prompt
- Consistent values across all paths

## Files Modified
- `info_agent/services/call_data_extractor.py`
