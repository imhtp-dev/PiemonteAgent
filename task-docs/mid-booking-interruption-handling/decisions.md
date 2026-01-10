# Decisions: Mid-Booking Interruption Handling

## Decisions Made

### 1. Store NodeConfig directly vs recreate from params
**Decision**: Store `NodeConfig` directly in state

**Rationale**:
- Simpler - no factory functions needed
- Works for all node types
- NodeConfig is already a complete object
- Downside: slightly more memory, but negligible

### 2. Where to store booking position
**Decision**: Store at each node transition, not continuously

**Rationale**:
- Only need to resume from last "stable" position
- Don't need to track mid-conversation state
- Cleaner implementation

### 3. What to do when user says "transfer" mid-booking
**Decision**: Ask what they need → if they want to continue → resume booking

**Rationale**:
- Current behavior already asks "dimmi di cosa hai bisogno"
- Just need to add resumption logic after that
- User can still explicitly ask to abandon booking

## Pending Questions

1. **Should booking be abandoned if user explicitly requests transfer twice?**
   - Current: After first ask, we try to help
   - After second failure, transfer happens
   - Should this reset booking state?

2. **What if user asks question that requires transition (like start_booking for different service)?**
   - Should we allow starting new booking mid-booking?
   - Or should we ask "do you want to cancel current booking?"

3. **Should position be stored for EVERY node or just key nodes?**
   - Every node = more accurate resumption
   - Key nodes only = simpler implementation
   - Suggested: Start with key nodes, expand if needed

## Assumptions

1. User wants to continue booking after info question is answered
2. Stored NodeConfig remains valid throughout conversation (services/slots don't change)
3. `booking_in_progress` state flag is reliably set
