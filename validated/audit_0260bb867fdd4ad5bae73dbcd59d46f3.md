### Title
Lack of Slippage Protection in `MsgExitTierWithDelegation` Partial Exit Burns More Shares Than Expected After Validator Slash - (File: `x/tieredrewards/keeper/transfer_delegation.go`)

---

### Summary

`MsgExitTierWithDelegation` accepts a token `amount` the user wants to transfer back as a delegation. For a partial exit, the handler calls `ValidateUnbondAmount` to find the shares needed to produce exactly `amount` tokens at the **current** exchange rate. If the validator is slashed between the time the user signs the transaction and the time it is included in a block, the shares-per-token rate worsens, so more shares are burned than the user anticipated. The remaining position is silently smaller than intended, with no way for the user to bound the acceptable rate.

---

### Finding Description

`MsgExitTierWithDelegation` takes a single `amount math.Int` field — the number of tokens the user wants to transfer back to their own delegation. [1](#0-0) 

Inside `transferDelegationFromPosition`, for a partial exit the code calls `ValidateUnbondAmount` to convert the requested token amount into shares at the live exchange rate, then unbonds exactly those shares: [2](#0-1) 

The live exchange rate is `validator.TokensFromShares`, which decreases whenever the validator is slashed: [3](#0-2) 

**Concrete scenario:**

1. Alice has a position with 2 000 tokens (2 000 shares, 1:1 rate). She queries the position, decides to do a partial exit of 1 000 tokens, and broadcasts `MsgExitTierWithDelegation{amount: 1000}`.
2. While the transaction sits in the mempool, the validator is slashed 10 %. The exchange rate drops to 0.9 tokens/share; the position is now worth 1 800 tokens.
3. The transaction is included. `validateExitTierWithDelegation` checks `1000 <= 1800` — passes.
4. `ValidateUnbondAmount(posDelAddr, valAddr, 1000)` returns ≈ 1 111 shares (the shares needed to yield 1 000 tokens at the 0.9 rate).
5. 1 111 shares are unbonded and re-delegated to Alice. Remaining position: 889 shares ≈ 800 tokens.

Alice expected to keep 1 000 tokens in the position but is left with only ≈ 800 tokens. She had no way to express "only proceed if the remaining position is at least X tokens" or "only proceed if the exchange rate is at least Y".

The `MsgExitTierWithDelegation` message struct contains no `min_remaining_amount` or `min_exchange_rate` field: [4](#0-3) 

The ADR confirms the partial-exit path relies on the live `TokensFromShares` value with no slippage guard: [5](#0-4) 

---

### Impact Explanation

For a partial exit, the user burns more shares than expected to obtain the requested token amount. The remaining position is silently smaller than intended. If the remaining position falls below `tier.MinLockAmount`, the transaction reverts with `ErrMinLockAmountNotMet`, forcing the user to either accept a full exit (losing all remaining position value at the slashed rate) or wait. If it stays above `MinLockAmount`, the user silently loses a portion of their locked position value with no recourse. In both cases the user cannot enforce a minimum acceptable outcome. [6](#0-5) 

---

### Likelihood Explanation

Validator slashes (double-sign, extended downtime) are real, recurring events on Cosmos POS chains. Cosmos SDK transactions can remain in the mempool for multiple blocks. A slash event processed in `BeginBlocker` of block N will affect any `MsgExitTierWithDelegation` that was signed against the pre-slash state and lands in block N or later. No special privileges are required; any position owner performing a partial exit is exposed. [7](#0-6) 

---

### Recommendation

Add an optional `min_remaining_amount math.Int` field to `MsgExitTierWithDelegation`. After computing `remainingPositionAmount` in the partial-exit branch, reject the transaction if `remainingPositionAmount < msg.MinRemainingAmount`. This mirrors the EIP-4626 recommendation cited in the external report and gives users a way to bound the worst-case outcome without breaking the existing interface for callers that leave the field at zero. [8](#0-7) 

---

### Proof of Concept

```
State before tx:
  validator exchange rate: 1.0 tokens/share
  position shares:         2000
  position token value:    2000

Alice signs: MsgExitTierWithDelegation{position_id: 1, amount: 1000}

BeginBlocker of next block:
  validator slashed 10%
  new exchange rate: 0.9 tokens/share
  position token value: 1800

Alice's tx executes in same block:
  validateExitTierWithDelegation: 1000 <= 1800  ✓
  ExitWithFullDelegation(1000, 1800) = false  → partial path
  ValidateUnbondAmount(posDelAddr, valAddr, 1000)
    → shares_needed = ceil(1000 / 0.9) ≈ 1112 shares
  Unbond(posDelAddr, valAddr, 1112 shares) → 1000 tokens
  Delegate(owner, 1000 tokens, validator)

Remaining position:
  shares: 2000 - 1112 = 888
  token value: 888 × 0.9 ≈ 799 tokens   ← Alice expected 1000
``` [9](#0-8) [10](#0-9)

### Citations

**File:** proto/chainmain/tieredrewards/v1/tx.proto (L347-390)
```text
// MsgExitTierWithDelegation exits a tier position by transferring the module's
// delegation back to the owner on the same validator. No unbonding period.
// Supports partial exits: only the specified amount is transferred.
// If the full amount is transferred, the position is deleted.
message MsgExitTierWithDelegation {
  option (cosmos.msg.v1.signer) = "owner";
  option (amino.name)           = "chainmain/MsgExitTierWithDelegation";

  // owner is the position owner's address.
  string owner = 1 [(cosmos_proto.scalar) = "cosmos.AddressString"];

  // position_id is the ID of the position to exit.
  uint64 position_id = 2;

  // amount is the amount of tokens to transfer back to the owner's delegation.
  string amount = 3 [
    (cosmos_proto.scalar)  = "cosmos.Int",
    (gogoproto.customtype) = "cosmossdk.io/math.Int",
    (gogoproto.nullable)   = false,
    (amino.dont_omitempty) = true
  ];
}

// MsgExitTierWithDelegationResponse defines the response for MsgExitTierWithDelegation.
message MsgExitTierWithDelegationResponse {
  // position_id echoes the exited position ID.
  uint64 position_id = 1;

  // transferred_amount is the actual tokens transferred back to the owner's delegation.
  string transferred_amount = 2 [
    (cosmos_proto.scalar)  = "cosmos.Int",
    (gogoproto.customtype) = "cosmossdk.io/math.Int",
    (gogoproto.nullable)   = false,
    (amino.dont_omitempty) = true
  ];
  // transferred_shares is the shares transferred back to the owner's delegation.
  string transferred_shares = 3 [
    (cosmos_proto.scalar)  = "cosmos.Dec",
    (gogoproto.customtype) = "cosmossdk.io/math.LegacyDec",
    (gogoproto.nullable)   = false
  ];

  // full_exit is true when the entire position was transferred and deleted.
  bool full_exit = 4;
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L136-169)
```go
	positionAmount, err := k.reconcileAmountFromShares(ctx, valAddr, pos.Delegation.Shares)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	unbondedShares := pos.Delegation.Shares
	if !pos.ExitWithFullDelegation(amount, positionAmount) {
		unbondedShares, err = k.stakingKeeper.ValidateUnbondAmount(ctx, posDelAddr, valAddr, amount)
		if err != nil {
			return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
		}
	}

	transferredAmount, err := k.stakingKeeper.Unbond(ctx, posDelAddr, valAddr, unbondedShares)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	if transferredAmount.IsZero() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrTinyTransferDelegationAmount
	}

	// Re-fetch updated validator
	validator, err = k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	ownerNewShares, err := k.stakingKeeper.Delegate(ctx, owner, transferredAmount, validator.GetStatus(), validator, false)
	if err != nil {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, err
	}

	return ownerNewShares, unbondedShares, transferredAmount, nil
```

**File:** x/tieredrewards/keeper/delegation.go (L29-40)
```go
// reconcileAmountFromShares converts delegation shares to the actual withdrawable
// token amount under the validator's current exchange rate.
func (k Keeper) reconcileAmountFromShares(ctx context.Context, valAddr sdk.ValAddress, shares math.LegacyDec) (math.Int, error) {
	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return math.Int{}, err
	}
	if val.GetDelegatorShares().IsZero() {
		return math.ZeroInt(), nil
	}
	return val.TokensFromShares(shares).TruncateInt(), nil
}
```

**File:** doc/architecture/adr-006.md (L232-250)
```markdown
### MsgExitTierWithDelegation Flow

```
-> Validate: owner match, delegated, exit triggered, exit elapsed, amount > 0, amount <= position amount, no active redelegation
-> Claim rewards for position (settle base + bonus)
-> positionAmount = TokensFromShares(pos.Delegation.Shares)  // pre-transfer live value
-> If amount == positionAmount (full exit): unbondedShares = pos.Delegation.Shares
   Else (partial): unbondedShares = ValidateUnbondAmount(posDelAddr, valAddr, amount)
-> transferDelegationFromPosition: Unbond(posDelAddr, valAddr, unbondedShares) -> transferredAmount
   Re-fetch validator, Delegate(owner, transferredAmount, validator) — instant, no unbonding
-> If full exit:
     sweep the position's spendable bank balance (SpendableCoins, not GetAllBalances)
     from posDelAddr to owner.
     delete position (all indexes cleaned up, WithdrawAddr cleared via DeleteDelegatorWithdrawAddr)
   Else:
     remaining token value must meet tier.MinLockAmount (post-transfer check on actual amount)
     save position
-> Emit EventExitTierWithDelegation(position_id, tier_id, owner, validator, transferred_amount, transferred_shares, full_exit)
```
```

**File:** x/tieredrewards/keeper/msg_server.go (L543-551)
```go
	positionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}

	transferredShares, unbondedShares, transferredAmount, err := ms.transferDelegationFromPosition(ctx, pos, valAddr, msg.Amount)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L582-603)
```go
	} else {
		remainingShares := pos.Delegation.Shares.Sub(unbondedShares)
		// Compute remaining token value for min lock check.
		remainingPositionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, remainingShares)
		if err != nil {
			return nil, err
		}

		tier, err := ms.getTier(ctx, pos.TierId)
		if err != nil {
			return nil, err
		}
		// actual remaining amount (post-transfer) must meet min lock.
		if !tier.MeetsMinLockRequirement(remainingPositionAmount) {
			return nil, errorsmod.Wrapf(types.ErrMinLockAmountNotMet,
				"remaining amount %s is below tier minimum %s", remainingPositionAmount, tier.MinLockAmount)
		}

		if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/msg_server_exit_tier_with_delegation_test.go (L420-474)
```go
// TestMsgExitTierWithDelegation_FullExitAfterSlash verifies that a full exit
// after a validator slash (non-1:1 exchange rate) works correctly. The user
// passes the post-slash token value and ExitWithFullDelegation returns true,
// so all DelegatedShares are used directly (no ValidateUnbondAmount truncation).
func (s *KeeperSuite) TestMsgExitTierWithDelegation_FullExitAfterSlash() {
	lockAmount := sdkmath.NewInt(10000)
	pos := s.setupNewTierPosition(lockAmount, true)
	_, bondDenom := s.getStakingData()
	s.fundRewardsPool(sdkmath.NewInt(1_000_000), bondDenom)

	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)
	ownerAddr := sdk.MustAccAddressFromBech32(pos.Owner)
	posDelAddr := sdk.MustAccAddressFromBech32(pos.DelegatorAddress)

	// Slash 10% to create a non-1:1 exchange rate.
	s.ctx = s.ctx.WithBlockHeight(s.ctx.BlockHeight() + 1)
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(time.Hour))
	s.slashValidatorDirect(valAddr, sdkmath.LegacyNewDecWithPrec(10, 2))

	// Re-read position after slash hook.
	pos, err := s.keeper.GetPositionState(s.ctx, pos.Id)
	s.Require().NoError(err)
	s.Require().True(pos.IsDelegated())

	// Compute token value from shares (post-slash).
	positionAmount, err := s.keeper.GetPositionAmount(s.ctx, pos)
	s.Require().NoError(err)
	s.Require().True(positionAmount.LT(lockAmount), "token value should be reduced after slash")

	s.advancePastExitDuration()

	// Full exit using post-slash token value.
	msgServer := keeper.NewMsgServerImpl(s.keeper)
	resp, err := msgServer.ExitTierWithDelegation(s.ctx, &types.MsgExitTierWithDelegation{
		Owner:      pos.Owner,
		PositionId: pos.Id,
		Amount:     positionAmount,
	})
	s.Require().NoError(err)
	s.Require().True(resp.FullExit)
	s.Require().True(resp.TransferredAmount.IsPositive())

	// Position should be deleted.
	_, err = s.keeper.GetPositionState(s.ctx, pos.Id)
	s.Require().ErrorIs(err, types.ErrPositionNotFound)

	// Owner should have a staking delegation.
	del, err := s.app.StakingKeeper.GetDelegation(s.ctx, ownerAddr, valAddr)
	s.Require().NoError(err)
	s.Require().True(del.Shares.IsPositive())

	// Position's delegator address should have no remaining delegation after full exit.
	_, err = s.app.StakingKeeper.GetDelegation(s.ctx, posDelAddr, valAddr)
	s.Require().Error(err, "position's delegation should be fully removed after full exit")
}
```
