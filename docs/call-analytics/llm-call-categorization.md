# LLM-Based Call Categorization (esito_chiamata + motivazione)

## Problem

Call categorization was using mix of LLM + if/else overrides:
1. LLM analyzed calls but code hardcoded values for transfers
2. Client had to do post-processing because values were inaccurate
3. Rule-based logic couldn't distinguish nuanced cases (e.g., "Mancata comprensione" vs "Argomento sconosciuto")
4. LLM sometimes output "Info. fornite" instead of "Info fornite"

## Solution

Let LLM fully decide `esito_chiamata` and `motivazione` with:
1. Strict enum validation
2. Rich examples in prompt
3. Normalization for LLM variations

### Valid Values (Strict Enums)

```python
VALID_ESITO_CHIAMATA = ["COMPLETATA", "TRASFERITA", "NON COMPLETATA"]

VALID_MOTIVAZIONE = {
    "COMPLETATA": ["Info fornite", "Pren. effettuata"],
    "TRASFERITA": ["Mancata comprensione", "Argomento sconosciuto", "Richiesta paziente", "Prenotazione"],
    "NON COMPLETATA": ["Interrotta dal paziente", "Fuori orario", "Problema Tecnico"]
}
```

### Combinations with Descriptions

| esito_chiamata | motivazione | Description |
|----------------|-------------|-------------|
| COMPLETATA | Info fornite | AI responded successfully to patient request |
| COMPLETATA | Pren. effettuata | AI booked patient autonomously |
| TRASFERITA | Mancata comprensione | AI didn't understand patient's question |
| TRASFERITA | Argomento sconosciuto | AI lacks knowledge on topic |
| TRASFERITA | Richiesta paziente | Patient requested human operator |
| TRASFERITA | Prenotazione | Transfer for booking |
| NON COMPLETATA | Interrotta dal paziente | Patient hung up unexpectedly |
| NON COMPLETATA | Fuori orario | Transfer needed but operators unavailable |
| NON COMPLETATA | Problema Tecnico | AI failed due to technical error |

### Validation Function

```python
def validate_and_fix_llm_output(analysis: Dict[str, Any]) -> Dict[str, Any]:
    # 1. Normalize LLM typos
    motivazione_fixes = {
        "Info. fornite": "Info fornite",  # LLM adds period
        "info fornite": "Info fornite",   # Case fix
    }

    # 2. Validate esito_chiamata
    if esito not in VALID_ESITO_CHIAMATA:
        esito = "COMPLETATA"  # Default

    # 3. Validate motivazione matches esito
    if motivazione not in VALID_MOTIVAZIONE[esito]:
        # If valid but wrong esito → fix esito to match
        # If invalid → use first valid for that esito
```

## Key Code Reference

- `info_agent/services/call_data_extractor.py`
  - Lines 20-29: Valid enum constants
  - Lines 32-92: `validate_and_fix_llm_output()` function
  - Lines 320-419: `_analyze_call_with_llm()` with enhanced prompt
  - Lines 421-461: `_get_fallback_analysis()` fallback
  - Lines 530-605: `analyze_for_transfer()` passes LLM values
  - Lines 607-774: `save_to_database()` uses LLM values (no hardcoding)

## LLM Prompt Structure

```
## CLASSIFICATION RULES

### ESITO_CHIAMATA + MOTIVAZIONE (STRICT - use ONLY these combinations):

**COMPLETATA** (call completed successfully):
- "Info fornite" → L'AI ha risposto con successo alla richiesta del paziente
- "Pren. effettuata" → L'AI ha gestito e prenotato il paziente in autonomia

**TRASFERITA** (call transferred to human operator):
- "Mancata comprensione" → AI non comprende o non è certa di aver compreso la domanda
- "Argomento sconosciuto" → AI non sa rispondere poichè non possiede conoscenza
- "Richiesta paziente" → Paziente ha richiesto di parlare con un operatore umano
- "Prenotazione" → Paziente viene trasferito per effettuare prenotazione

**NON COMPLETATA** (call ended without resolution):
- "Interrotta dal paziente" → Paziente interrompe in modo inaspettato la chiamata
- "Fuori orario" → AI deve trasferire ma operatori non disponibili
- "Problema Tecnico" → L'AI non è riuscita a rispondere a causa di un problema tecnico

CRITICAL: motivazione MUST be one of the exact values listed above.
```

## Gotchas

1. **"Info. fornite" vs "Info fornite"** - LLM adds periods to Italian abbreviations. Validation normalizes this.
2. **Transfer hardcoding removed** - Previously `save_to_database()` hardcoded `motivazione = "Richiesta paziente"` for all transfers
3. **Fallback also validated** - `_get_fallback_analysis()` runs through validation too
4. **Existing DB data unchanged** - Old records with "Info. fornite" need SQL migration if cleanup needed:
   ```sql
   UPDATE tb_stat SET motivazione = 'Info fornite' WHERE motivazione = 'Info. fornite';
   ```

## Date Learned
2024-12-24

## Related
- Task docs: `task-docs/llm-call-categorization/`
- Client requirements image showed all valid combinations
