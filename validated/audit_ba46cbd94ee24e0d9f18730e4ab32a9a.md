### Title
Hardcoded `bondDenom`-only fee-collector balance check in `topUpBaseRewards` causes unnecessary drain of the tiered rewards pool - (File: x/tieredrewards/keeper/abci.go)

### Summary

`topUpBaseRewards` in `x/tieredrewards/keeper/abci.go` computes the per-block staker reward shortfall by reading **only the `bondDenom` balance** of the fee-collector module account. Because the Cosmos SDK fee-collector accumulates fees in every denom that validators accept (including IBC tokens), any block whose fees are paid in non-bond denoms is treated as if zero fees were collected. The shortfall is therefore overestimated, and the `RewardsPoolName` module account is drained by the full overestimated amount every such block.

---

### Finding Description

In `topUpBaseRewards` the fee-collector balance is queried with a single-denom call:

```go
// x/tieredrewards/keeper/abci.go L73-75
feeCollectorBalance := k.bankKeeper.GetBalance(ctx, feeCollectorAddr, bondDenom)
defaultStakersRewardPerBlock := math.LegacyNewDecFromInt(feeCollectorBalance.Amount).
    MulTruncate(math.LegacyOneDec().Sub(communityTax))
```

`GetBalance` returns only the `basecro` balance. If the block's fees were paid in any other denom (e.g., an IBC-transferred token accepted by validators), `feeCollectorBalance.Amount` is zero even though the fee-collector holds real value. The shortfall is then computed as:

```go
// x/tieredrewards/keeper/abci.go L77
shortFallAmount := targetStakersRewardPerBlock.Sub(defaultStakersRewardPerBlock).TruncateInt()
```

With `defaultStakersRewardPerBlock == 0`, `shortFallAmount` equals the full `targetStakersRewardPerBlock`. The function then transfers that entire amount from the rewards pool to the distribution module:

```go
// x/tieredrewards/keeper/abci.go L113
err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
```

This transfer is unnecessary: the fee-collector already holds sufficient value to cover staker rewards, just in a different denom. The rewards pool loses `bondDenom` tokens that should have been preserved for tiered-rewards bonus payouts.

---

### Impact Explanation

The `RewardsPoolName` module account is the sole source of bonus rewards for all tiered-lock positions. Every block in which transaction fees are paid in a non-bond denom causes an unjustified transfer of `bondDenom` from the rewards pool to the distribution module. Over time this silently depletes the pool, reducing or eliminating the bonus rewards available to tiered-lock position holders. The corrupted value is the `bondDenom` balance of the `RewardsPoolName` module account.

---

### Likelihood Explanation

The Cronos POS Chain is IBC-enabled. Validators on IBC chains routinely configure `minimum-gas-prices` to accept IBC-denominated tokens as fees. Any standard `MsgSend`, `MsgDelegate`, or IBC transfer transaction submitted with fees in a non-bond IBC denom is sufficient to trigger the overestimation. No special privilege is required; any unprivileged user paying fees in an accepted non-bond denom triggers the drain every block.

---

### Recommendation

Replace the single-denom `GetBalance` call with a full balance query (`GetAllBalances`) and convert the non-bond-denom portion to its `bondDenom` equivalent (e.g., via an on-chain oracle or by simply summing only the `bondDenom` component of the fee-collector's total staker-destined value after the distribution module has already converted multi-denom fees). At minimum, document that the module assumes all fees are paid in `bondDenom` and enforce this assumption at the ante-handler level by rejecting non-bond-denom fees.

---

### Proof of Concept

1. Chain is configured with `minimum-gas-prices = "0.025basecro,0.01ibc/XXXX"` (a common IBC-chain setup).
2. A user submits any transaction (e.g., `MsgSend`) with fees paid entirely in `ibc/XXXX`.
3. At `BeginBlock`, `topUpBaseRewards` runs:
   - `feeCollectorBalance = GetBalance(feeCollectorAddr, "basecro")` → `0` (fees were in IBC denom).
   - `defaultStakersRewardPerBlock = 0`.
   - `shortFallAmount = targetStakersRewardPerBlock` (full target, nothing offset).
4. `SendCoinsFromModuleToModule(RewardsPoolName → distribution, shortFallAmount basecro)` executes.
5. The rewards pool loses `shortFallAmount` of `basecro` even though the fee-collector held equivalent value in `ibc/XXXX`.
6. Repeated every block with non-bond-denom fees, the rewards pool is drained at the full target rate regardless of actual fee revenue, starving tiered-lock bonus reward claimants. [1](#0-0) [2](#0-1)

### Citations

**File:** x/tieredrewards/keeper/abci.go (L73-77)
```go
	feeCollectorBalance := k.bankKeeper.GetBalance(ctx, feeCollectorAddr, bondDenom)
	defaultStakersRewardPerBlock := math.LegacyNewDecFromInt(feeCollectorBalance.Amount).
		MulTruncate(math.LegacyOneDec().Sub(communityTax))

	shortFallAmount := targetStakersRewardPerBlock.Sub(defaultStakersRewardPerBlock).TruncateInt()
```

**File:** x/tieredrewards/keeper/abci.go (L113-116)
```go
	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
	if err != nil {
		return err
	}
```
