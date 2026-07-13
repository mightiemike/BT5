### Title
No Governance Withdrawal Mechanism for the Tiered Rewards Pool — (`x/tieredrewards/keeper/msg_server_auth.go`)

### Summary
The `x/tieredrewards` module maintains a `RewardsPoolName` module account that accumulates bond-denom tokens used to pay bonus rewards and top up base staking rewards. There is no governance (or any other) message to withdraw excess funds from this pool. Once tokens are sent to the pool, they can only leave via two automatic outflow paths. If those paths are exhausted or disabled, the remaining balance is permanently locked with no recovery mechanism.

### Finding Description
The rewards pool module account (`types.RewardsPoolName`) has exactly two outflow paths:

1. **`BeginBlocker` top-up** — transfers bond-denom shortfall from the pool to `x/distribution` each block, but only when `TargetBaseRewardsRate > 0` and there is an actual shortfall.
2. **Bonus reward claims** — `processEventsAndClaimBonus` calls `SendCoinsFromModule(rewards_pool, owner, bonus)` when a position owner claims. [1](#0-0) 

The governance message handler in `msg_server_auth.go` exposes only four messages: `UpdateParams`, `AddTier`, `UpdateTier`, and `DeleteTier`. None of them touch the pool balance. [2](#0-1) 

There is no `MsgWithdrawFromPool` or equivalent message anywhere in the module's message set. [3](#0-2) 

The pool is a publicly-known module account address (derivable from `types.RewardsPoolName`), so any party can send tokens to it via a standard bank send. Integration tests confirm this is the intended funding mechanism. [4](#0-3) 

### Impact Explanation
Any bond-denom tokens in the pool that exceed what `BeginBlocker` will ever drain plus what active positions will ever claim as bonus are permanently locked. Concretely:

- If governance passes `MsgUpdateParams` setting `TargetBaseRewardsRate = 0`, the `BeginBlocker` outflow is disabled entirely.
- If all tier positions exit (naturally or via `CloseOnly` governance action), the bonus-claim outflow is exhausted.
- The remaining pool balance — which could be substantial if the pool was pre-funded for a multi-year reward program — is irrecoverable.

Additionally, any **non-bond-denom** tokens accidentally or intentionally sent to the pool address are permanently locked immediately, because neither outflow path handles non-bond-denom balances. [5](#0-4) [6](#0-5) 

The corrupted value is the `RewardsPoolName` module account balance.

### Likelihood Explanation
The pool is designed to be funded by the foundation or community treasury for long-running reward programs. Over-funding is a realistic operational outcome (e.g., a tier is deleted mid-program, reducing future bonus obligations, but the pre-funded tokens remain). Governance sunsetting the module (setting `CloseOnly` on all tiers and eventually setting rate to 0) is a normal lifecycle event that would leave residual pool funds permanently locked. No attacker action is required — normal protocol operation is sufficient.

### Recommendation
Add a governance-gated message to withdraw the pool balance to a specified recipient:

```go
func (ms msgServer) WithdrawRewardsPool(ctx context.Context, msg *types.MsgWithdrawRewardsPool) (*types.MsgWithdrawRewardsPoolResponse, error) {
    if err := ms.requireAuthority(msg.Authority); err != nil {
        return nil, err
    }
    recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
    if err != nil {
        return nil, err
    }
    poolAddr := ms.accountKeeper.GetModuleAddress(types.RewardsPoolName)
    balances := ms.bankKeeper.SpendableCoins(ctx, poolAddr)
    if balances.IsZero() {
        return &types.MsgWithdrawRewardsPoolResponse{}, nil
    }
    if err := ms.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, recipient, balances); err != nil {
        return nil, err
    }
    return &types.MsgWithdrawRewardsPoolResponse{Amount: balances}, nil
}
```

### Proof of Concept
1. Governance funds the rewards pool with 10,000,000 `basecro` for a 5-year bonus program.
2. After 2 years, governance votes to sunset the tieredrewards module: marks all tiers `CloseOnly` via `MsgUpdateTier`, then sets `TargetBaseRewardsRate = 0` via `MsgUpdateParams`.
3. All existing positions exit over the following year via `MsgExitTierWithDelegation` or `MsgWithdrawFromTier`.
4. The pool still holds the unspent portion of the 10,000,000 `basecro` pre-fund.
5. No message exists to recover these funds. They are permanently locked in the `RewardsPoolName` module account. [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/abci.go (L30-34)
```go
	targetBaseRewardsRate := params.TargetBaseRewardsRate

	if targetBaseRewardsRate.IsZero() {
		return nil
	}
```

**File:** x/tieredrewards/keeper/abci.go (L41-44)
```go
	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		panic(fmt.Sprintf("failed to get bond denom: %v", err))
	}
```

**File:** x/tieredrewards/keeper/abci.go (L96-113)
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

	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L21-103)
```go
func (ms msgServer) UpdateParams(ctx context.Context, msg *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	if err := ms.SetParams(ctx, msg.Params); err != nil {
		return nil, err
	}

	return &types.MsgUpdateParamsResponse{}, nil
}

func (ms msgServer) AddTier(ctx context.Context, msg *types.MsgAddTier) (*types.MsgAddTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	has, err := ms.hasTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}
	if has {
		return nil, types.ErrTierAlreadyExists
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_NEW, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgAddTierResponse{}, nil
}

func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	oldTier, err := ms.getTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}

	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_UPDATE, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgUpdateTierResponse{}, nil
}

func (ms msgServer) DeleteTier(ctx context.Context, msg *types.MsgDeleteTier) (*types.MsgDeleteTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, msg.Id)
	if err != nil {
		return nil, err
	}

	if err := ms.deleteTier(ctx, msg.Id); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_DELETE, tier); err != nil {
		return nil, err
	}

	return &types.MsgDeleteTierResponse{}, nil
}
```

**File:** x/tieredrewards/types/codec.go (L1-50)
```go
package types

import (
	"github.com/cosmos/cosmos-sdk/codec"
	"github.com/cosmos/cosmos-sdk/codec/legacy"
	cdctypes "github.com/cosmos/cosmos-sdk/codec/types"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/cosmos/cosmos-sdk/types/msgservice"
)

func RegisterLegacyAminoCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(Params{}, "chainmain/tieredrewards/Params", nil)
	legacy.RegisterAminoMsg(cdc, &MsgUpdateParams{}, "chainmain/tieredrewards/MsgUpdateParams")
	legacy.RegisterAminoMsg(cdc, &MsgAddTier{}, "chainmain/MsgAddTier")
	legacy.RegisterAminoMsg(cdc, &MsgUpdateTier{}, "chainmain/MsgUpdateTier")
	legacy.RegisterAminoMsg(cdc, &MsgDeleteTier{}, "chainmain/MsgDeleteTier")
	legacy.RegisterAminoMsg(cdc, &MsgLockTier{}, "chainmain/MsgLockTier")
	legacy.RegisterAminoMsg(cdc, &MsgCommitDelegationToTier{}, "chainmain/MsgCommitDelegationToTier")
	legacy.RegisterAminoMsg(cdc, &MsgTierUndelegate{}, "chainmain/MsgTierUndelegate")
	legacy.RegisterAminoMsg(cdc, &MsgTierRedelegate{}, "chainmain/MsgTierRedelegate")
	legacy.RegisterAminoMsg(cdc, &MsgAddToTierPosition{}, "chainmain/MsgAddToTierPosition")
	legacy.RegisterAminoMsg(cdc, &MsgTriggerExitFromTier{}, "chainmain/MsgTriggerExitFromTier")
	legacy.RegisterAminoMsg(cdc, &MsgClearPosition{}, "chainmain/MsgClearPosition")
	legacy.RegisterAminoMsg(cdc, &MsgClaimTierRewards{}, "chainmain/MsgClaimTierRewards")
	legacy.RegisterAminoMsg(cdc, &MsgWithdrawFromTier{}, "chainmain/MsgWithdrawFromTier")
	legacy.RegisterAminoMsg(cdc, &MsgExitTierWithDelegation{}, "chainmain/MsgExitTierWithDelegation")
}

func RegisterInterfaces(registry cdctypes.InterfaceRegistry) {
	registry.RegisterImplementations(
		(*sdk.Msg)(nil),
		&MsgUpdateParams{},
		&MsgAddTier{},
		&MsgUpdateTier{},
		&MsgDeleteTier{},
		&MsgLockTier{},
		&MsgCommitDelegationToTier{},
		&MsgTierUndelegate{},
		&MsgTierRedelegate{},
		&MsgAddToTierPosition{},
		&MsgTriggerExitFromTier{},
		&MsgClearPosition{},
		&MsgClaimTierRewards{},
		&MsgWithdrawFromTier{},
		&MsgExitTierWithDelegation{},
	)

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}

```

**File:** integration_tests/tieredrewards_helpers.py (L140-144)
```python
def fund_pool(cluster, from_name, amount_coin):
    """Fund the rewards pool via a bank send to the module account."""
    from_addr = cluster.address(from_name)
    pool_addr = module_address(REWARDS_POOL_NAME)
    return cluster.transfer(from_addr, pool_addr, amount_coin)
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L1-30)
```go
package keeper

import (
	"context"
	"time"

	"github.com/crypto-org-chain/chain-main/v8/x/tieredrewards/types"

	errorsmod "cosmossdk.io/errors"
	"cosmossdk.io/math"

	sdk "github.com/cosmos/cosmos-sdk/types"
)

func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}

// computeSegmentBonus computes bonus for a time segment using a snapshot rate.
// Formula: shares * tokensPerShare * tier.BonusApy * durationSeconds / SecondsPerYear
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier, segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}

	if !segmentEnd.After(segmentStart) {
```
