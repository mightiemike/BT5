### Title
`topUpBaseRewards` Shortfall Accounting Omits Current-Block Transaction Fees, Over-Draining the Rewards Pool - (File: `x/tieredrewards/keeper/abci.go`)

---

### Summary

`topUpBaseRewards` computes the per-block staker reward shortfall using the fee-collector balance at BeginBlocker time. Because transaction fees are only deposited into the fee collector **during** transaction processing (after all BeginBlockers complete), the balance read at BeginBlocker time never includes the current block's transaction fees. The shortfall is therefore systematically overestimated by `txFees × (1 − communityTax)` every block that carries non-zero fees, causing the rewards pool to be over-drained by that same amount each block.

---

### Finding Description

`topUpBaseRewards` is the tieredrewards BeginBlocker. Its purpose is to top up staker rewards from the rewards pool only when the fee collector cannot cover the target rate on its own.

The shortfall is computed as:

```go
// x/tieredrewards/keeper/abci.go L74-77
feeCollectorBalance := k.bankKeeper.GetBalance(ctx, feeCollectorAddr, bondDenom)
defaultStakersRewardPerBlock := math.LegacyNewDecFromInt(feeCollectorBalance.Amount).
    MulTruncate(math.LegacyOneDec().Sub(communityTax))
shortFallAmount := targetStakersRewardPerBlock.Sub(defaultStakersRewardPerBlock).TruncateInt()
``` [1](#0-0) 

The BeginBlocker execution order is fixed in `app/app.go`:

```go
// app/app.go L615-619
app.ModuleManager.SetOrderBeginBlockers(
    upgradetypes.ModuleName,
    minttypes.ModuleName,
    tieredrewardstypes.ModuleName, // has to be after mint module and before distribution module
    distrtypes.ModuleName,
    ...
``` [2](#0-1) 

The EndBlocker order places distribution **after** all transactions have been processed:

```go
// app/app.go L641-648
app.ModuleManager.SetOrderEndBlockers(
    govtypes.ModuleName,
    stakingtypes.ModuleName,
    ...
    distrtypes.ModuleName,
``` [3](#0-2) 

The per-block lifecycle is therefore:

| Phase | Fee Collector Contents |
|---|---|
| Previous block EndBlocker: `distribution.AllocateTokens` | Drained to zero |
| Current block BeginBlocker: `mint` | + minted inflation tokens |
| Current block BeginBlocker: **`tieredrewards`** reads balance | = minted tokens only |
| Current block transactions processed | + transaction fees |
| Current block EndBlocker: `distribution.AllocateTokens` | Drained (minted + tx fees) |

When `tieredrewards` reads the fee collector at step 3, it sees only the minted tokens. The transaction fees that will arrive in step 4 are invisible to the shortfall calculation. The shortfall is therefore:

```
computed shortfall = target − mintedTokens × (1 − communityTax)
actual shortfall   = target − (mintedTokens + txFees) × (1 − communityTax)
over-drain per block = txFees × (1 − communityTax)
```

The integration test comment at line 266 confirms the design only accounts for minted tokens:

> "After mint runs, the fee collector holds the freshly minted coins. tieredrewards checks this balance and sees no shortfall, so the pool stays untouched." [4](#0-3) 

No test covers the case where both minted tokens AND transaction fees are present simultaneously, leaving the over-drain undetected.

---

### Impact Explanation

Every block that contains non-zero transaction fees causes the rewards pool (`RewardsPoolName` module account) to be debited by `txFees × (1 − communityTax)` more than the true shortfall. Stakers receive both the top-up amount AND the full transaction fees from distribution, exceeding the `TargetBaseRewardsRate`. The rewards pool — a finite resource funded by governance or protocol allocation — depletes faster than the protocol intends. The exact corrupted value is `RewardsPool.balance`, which is reduced by more than the actual shortfall each block.

---

### Likelihood Explanation

The trigger is any block with non-zero transaction fees, which is normal production operation. Any unprivileged user submitting a standard Cosmos SDK transaction (bank send, delegation, governance vote, IBC transfer, tieredrewards message, etc.) with a non-zero fee causes the over-drain. No special role, key, or configuration is required. The effect is continuous and cumulative across every block.

---

### Recommendation

Move the shortfall calculation to EndBlocker (after transaction fees have been collected), or read the fee collector balance after all BeginBlockers have run but before transactions are processed. Alternatively, augment `defaultStakersRewardPerBlock` with an estimate of the current block's expected transaction fees (e.g., using the previous block's fee collector balance before distribution drained it). The most robust fix is to move the top-up logic to EndBlocker, where the fee collector balance already reflects both minted tokens and the current block's transaction fees, so the shortfall is computed on the actual final balance.

---

### Proof of Concept

1. Deploy chain with `TargetBaseRewardsRate > 0` and a funded rewards pool.
2. Fund the rewards pool with a known amount `P`.
3. Submit N blocks, each containing one transaction with fee `F` basecro.
4. Observe that the rewards pool is drained by approximately `N × (targetPerBlock + F × (1 − communityTax))` instead of `N × max(0, targetPerBlock − mintedPerBlock × (1 − communityTax))`.
5. The pool depletes in fewer blocks than the protocol intends, confirmed by comparing `_pool_balance` before and after against the expected drain rate.

The integration test `TOPUP_FULL_SHORTFALL_BASECRO = "31688"` is computed assuming the fee collector holds only minted tokens. In a live network with active transaction fees, the actual per-block drain from the pool will exceed this value by `txFees × (1 − communityTax)`, confirming the over-drain. [5](#0-4) [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/abci.go (L63-77)
```go
	targetStakersRewardPerBlock := math.LegacyNewDecFromInt(totalBonded).
		Mul(targetBaseRewardsRate).
		Quo(math.LegacyNewDec(int64(blocksPerYear)))

	feeCollector := k.accountKeeper.GetModuleAccount(ctx, authtypes.FeeCollectorName)
	if feeCollector == nil {
		k.logger(ctx).Error("fee collector module account not found, skipping base rewards top up")
		return nil
	}
	feeCollectorAddr := feeCollector.GetAddress()
	feeCollectorBalance := k.bankKeeper.GetBalance(ctx, feeCollectorAddr, bondDenom)
	defaultStakersRewardPerBlock := math.LegacyNewDecFromInt(feeCollectorBalance.Amount).
		MulTruncate(math.LegacyOneDec().Sub(communityTax))

	shortFallAmount := targetStakersRewardPerBlock.Sub(defaultStakersRewardPerBlock).TruncateInt()
```

**File:** app/app.go (L615-619)
```go
	app.ModuleManager.SetOrderBeginBlockers(
		upgradetypes.ModuleName,
		minttypes.ModuleName,
		tieredrewardstypes.ModuleName, // has to be after mint module and before distribution module to calculate how much more base rewards to distribute
		distrtypes.ModuleName,
```

**File:** app/app.go (L641-648)
```go
	app.ModuleManager.SetOrderEndBlockers(
		govtypes.ModuleName,
		stakingtypes.ModuleName,
		ibcexported.ModuleName,
		ibctransfertypes.ModuleName,
		authtypes.ModuleName,
		banktypes.ModuleName,
		distrtypes.ModuleName,
```

**File:** integration_tests/test_base_rewards_top_up.py (L22-23)
```python
# Per-block top-up when fee collector is ~0: trunc(2e9 * 1.0 / 63115); see abci.go.
TOPUP_FULL_SHORTFALL_BASECRO = "31688"
```

**File:** integration_tests/test_base_rewards_top_up.py (L263-268)
```python

    BeginBlocker order: mint → tieredrewards → distribution.  After mint runs,
    the fee collector holds the freshly minted coins.  tieredrewards checks
    this balance and sees no shortfall, so the pool stays untouched.
    """
    cluster = inflation_cluster
```
