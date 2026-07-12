[File: 'x/tieredrewards/types/position.go -> Scope: Critical'] [Function: BeforeValidatorSlashed / BeforeRedelegationSlashed / processEventsAndClaimBonus] Can a position that is currently in an active redelegation receive both a BeforeValidatorSlashed event (for the destination validator) and a BeforeRedelegationSlashed event (for the redelegation entry) in the same block under the precondition that the destination validator is slashed while the redelegation is still active, triggering the call sequence BeforeValidatorSlashed(dstVal) -> appendValidatorEvent(SLASH) -> BeforeRedelegationSlashed(unbondingId) -> slashRedelegationPosition -> processEventsAndClaimBonus (processes the SLASH event just appended), violating the invariant that bonus settlement for a slash event must occur exactly once per position per slash, causing the position to settle bonus twice for the same slash event and overpay from the rewards pool? Proof idea: write a keeper integration test that slashes a validator while a position has an active redelegation to it, and asserts that processEventsAndClaim

### Citations

**File:** x/tieredrewards/types/position.go (L1-110)
```go
package types

import (
