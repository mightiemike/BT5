### Title
Non-Bond-Denom Tokens Sent to `rewards_pool` Module Account Are Permanently Stuck — (`app/app.go`, `x/tieredrewards/keeper/abci.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

The `rewards_pool` module account in `x/tieredrewards` is explicitly whitelisted to receive external funds from any user via `MsgSend`. However, the module only ever consumes the bond denom from this account. Any other token denom sent to the `rewards_pool` is permanently irrecoverable: the module holds no Burner permission, and no governance message or admin path exists to withdraw non-bond-denom balances.

---

### Finding Description

`app/app.go` registers two module accounts with a custom bank send restriction bypass:

```go
moduleAccsAllowedToReceiveExternalFunds = map[string]bool{
    tieredrewardstypes.RewardsPoolName: true,
    tieredrewardstypes.ModuleName:      true,  // legacy, not used anymore
}
``` [1](#0-0) 

The `rewards_pool` module account is registered with `nil` permissions — no `Minter`, no `Burner`, no `Staking`:

```go
tieredrewardstypes.RewardsPoolName: nil,
``` [2](#0-1) 

The only two code paths that move funds **out** of the `rewards_pool` are:

1. `BeginBlocker` → `topUpBaseRewards`: reads `poolBalance` for `bondDenom` only and sends to the distribution module. [3](#0-2) 

2. `processEventsAndClaimBonus`: constructs `bonusCoins` exclusively from `bondDenom` and calls `SendCoinsFromModuleToAccount`. [4](#0-3) 

Neither path iterates over all balances or handles non-bond-denom tokens. There is no `MsgWithdrawFromRewardsPool`, no governance sweep message, and no admin function anywhere in the module that touches non-bond-denom balances at the `rewards_pool` address.

The legacy `tieredrewards` module account (also in `moduleAccsAllowedToReceiveExternalFunds`) is in the same position: it is explicitly noted as "not used anymore" yet remains whitelisted to receive external funds, with only a `Staking` permission and no withdrawal path. [5](#0-4) 

---

### Impact Explanation

Any token denom other than the bond denom sent to the `rewards_pool` module account via a standard `MsgSend` transaction is permanently locked. The module account has no Burner permission, so `BurnCoins` is unavailable. No module-level message exists to recover these tokens. The exact corrupted value is the non-bond-denom balance at the `rewards_pool` module address, which accumulates without any drain path.

---

### Likelihood Explanation

The `moduleAccsAllowedToReceiveExternalFunds` bypass is intentional and documented — it is the mechanism by which operators and community members fund the rewards pool. The integration test confirms this path works for any coin string: [6](#0-5) 

Any user who mistakenly sends an IBC voucher, a governance token, or any non-bond denom to the pool address (e.g., by copy-pasting the address into a generic transfer) permanently loses those funds. The likelihood is low for a deliberate attacker but realistic for an accidental user given the pool address is publicly advertised for funding.

---

### Recommendation

1. Add a governance-gated `MsgSweepRewardsPool` message that allows the `x/gov` authority to send any non-bond-denom balance from the `rewards_pool` to the community pool or a specified address.
2. Alternatively, add a denom allowlist check inside the bank send restriction so that only the bond denom can be sent to the `rewards_pool` module account, rejecting all other denoms at the `MsgSend` level.
3. Remove the legacy `tieredrewards` module account from `moduleAccsAllowedToReceiveExternalFunds` entirely, since it is no longer used and has no outflow path.

---

### Proof of Concept

1. Query the `rewards_pool` module address (deterministic SHA-256 of `"rewards_pool"`).
2. Submit a `MsgSend` from any funded account sending, e.g., `1000ibc/XXXX` to the `rewards_pool` address. The transaction succeeds because the address is in `moduleAccsAllowedToReceiveExternalFunds`.
3. Query the `rewards_pool` balance — it now holds `1000ibc/XXXX`.
4. Wait indefinitely. `BeginBlocker` only drains `bondDenom`. `ClaimTierRewards` only pays out `bondDenom`. No other code path touches the `ibc/XXXX` balance.
5. The `1000ibc/XXXX` is permanently stuck: the module has no Burner permission and no message handler to recover it. [7](#0-6) [8](#0-7)

### Citations

**File:** app/app.go (L162-162)
```go
		tieredrewardstypes.RewardsPoolName: nil,
```

**File:** app/app.go (L163-173)
```go
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: {authtypes.Staking},
	}
	// moduleAccsAllowedToReceiveExternalFunds defines module accounts that can
	// receive tokens from external accounts via MsgSend, bypassing the default
	// block on sends to module accounts.
	moduleAccsAllowedToReceiveExternalFunds = map[string]bool{
		tieredrewardstypes.RewardsPoolName: true,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: true,
	}
```

**File:** x/tieredrewards/keeper/abci.go (L41-44)
```go
	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		panic(fmt.Sprintf("failed to get bond denom: %v", err))
	}
```

**File:** x/tieredrewards/keeper/abci.go (L96-111)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-241)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** integration_tests/test_base_rewards_top_up.py (L97-109)
```python
    # Fund the pool from signer1
    pool_addr = module_address(REWARDS_POOL_NAME)
    sf = int(TOPUP_FULL_SHORTFALL_BASECRO)
    # Enough for several top-ups; leave balance for test_pool_drains_to_zero.
    fund_amount = sf * 10
    fund_amount_coin = f"{fund_amount}basecro"

    rsp = cluster.transfer(
        cluster.address("signer1"),
        pool_addr,
        fund_amount_coin,
    )
    assert rsp["code"] == 0, rsp["raw_log"]
```

**File:** x/tieredrewards/types/keys.go (L20-23)
```go
const (
	ModuleName      = "tieredrewards"
	StoreKey        = ModuleName
	RewardsPoolName = "rewards_pool"
```
