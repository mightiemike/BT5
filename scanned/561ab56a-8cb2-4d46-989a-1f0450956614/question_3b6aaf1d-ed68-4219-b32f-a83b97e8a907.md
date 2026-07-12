[File: 'x/supply/types/keys.go -> Scope: Critical. Tiered rewards position, delegation, redelegation, slashing, exit, withdrawal, or reward-accounting flaw lets an attacker withdraw delegated stake, claim rewards, or move voting power not owned by them.'] [Function: msgServer.TierRedelegate / reindexPositionCountByValidator] Can an attacker under the precondition that they own a position on valA call TierRedelegate to valB, triggering TierRedelegate -> setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}) -> setPositionWithState -> reindexPositionCountByValidator(from=valA, to=valB_from_live_delegation), where the live delegation after

### Citations

**File:** x/supply/types/keys.go (L1-12)
```go
package types

const (
	// ModuleName defines the module name
	ModuleName =
