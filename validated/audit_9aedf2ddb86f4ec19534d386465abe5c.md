### Title
Bonus Reward Tokens Can Be Permanently Locked in `RewardsPoolName` Module Account With No Recovery Path — (`x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

The `tieredrewards` module accumulates bonus reward tokens in the `RewardsPoolName` module account. The **only** mechanism to withdraw tokens from this pool is `processEventsAndClaimBonus`, which requires active, delegated tier positions. No governance-gated or admin function exists to drain residual or excess pool funds. If all positions exit or if the pool is overfunded relative to earned rewards, the surplus tokens are permanently locked with no recovery path — a direct structural analog to the NukeFund issue.

---

### Finding Description

The `RewardsPoolName` module account is the sole repository for bonus rewards in the tiered rewards system. Tokens flow **into** the pool from the mint/inflation keeper (the `Keeper` holds a `mintKeeper` dependency) and flow **out** only through one path: [1](#0-0) 

```go
if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
    return nil, err
}
```

This outflow is gated by `sufficientBonusPoolBalance`, which confirms the pool must hold enough tokens before any payout: [2](#0-1) 

Critically, `processEventsAndClaimBonus` only executes when a position `IsDelegated()`: [3](#0-2) 

This means the pool can only be drained while active delegated positions exist. Once all positions are closed, the outflow path is permanently severed.

Examining every message handler in `msg_server.go`, none provides an admin or governance-gated function to withdraw from `RewardsPoolName`: [4](#0-3) 

The `WithdrawFromTier` handler, which is the final step of a position lifecycle, only sends the **position delegator account's spendable balance** to the owner — it does not touch `RewardsPoolName` at all: [5](#0-4) 

The `force_exit.go` helper (`ForceFullExitWithDelegation`) is explicitly marked for deletion after the v8 upgrade and is not a production-accessible message: [6](#0-5) 

---

### Impact Explanation

Any tokens minted into `RewardsPoolName` that exceed the total bonus earned and claimed by all positions before they exit are **permanently locked** in the module account. There is no governance proposal, admin message, or protocol-level mechanism to recover them. The broken invariant is:

> `RewardsPoolName.balance` must always be drainable to zero via normal protocol operation.

This invariant is violated whenever the pool is overfunded relative to active positions — a realistic outcome given continuous inflation-based funding and voluntary position exits.

---

### Likelihood Explanation

The `Keeper` holds a `mintKeeper` dependency, indicating the pool is funded automatically by the inflation/mint module: [7](#0-6) 

Continuous automatic funding combined with voluntary user exits creates a persistent and growing residual balance. Any user can trigger the lock condition simply by being the last position holder to exit — a normal, unprivileged, production-reachable action via `MsgExitTierWithDelegation` or `MsgTierUndelegate` + `MsgWithdrawFromTier`.

---

### Recommendation

Add a governance-gated message (authority-checked, analogous to the `authority` field already present in the keeper) that allows the chain's governance account to withdraw excess funds from `RewardsPoolName` — either to the community pool or to a specified address. This mirrors the standard Cosmos SDK pattern for module account fund recovery and directly addresses the missing escape hatch. [8](#0-7) 

---

### Proof of Concept

1. The inflation/mint module continuously mints tokens into `RewardsPoolName` each block.
2. Users create tier positions via `MsgLockTier` or `MsgCommitDelegationToTier`.
3. Users earn bonus rewards proportional to their locked duration and tier APY.
4. All users exit their positions: either via `MsgExitTierWithDelegation` (full exit) or `MsgTierUndelegate` followed by `MsgWithdrawFromTier`. Each exit path calls `claimRewards` before deletion, draining only the **earned** portion.
5. After all positions are deleted, `RewardsPoolName` retains the **unearned residual** — the difference between total minted and total claimed.
6. No message exists to withdraw this residual. The tokens are permanently locked. [9](#0-8) [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L143-146)
```go
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```

**File:** x/tieredrewards/keeper/msg_server.go (L1-30)
```go
package keeper

import (
	"context"

	"github.com/crypto-org-chain/chain-main/v8/x/tieredrewards/types"

	errorsmod "cosmossdk.io/errors"

	sdk "github.com/cosmos/cosmos-sdk/types"
	sdkerrors "github.com/cosmos/cosmos-sdk/types/errors"
)

var _ types.MsgServer = msgServer{}

type msgServer struct {
	Keeper
}

func NewMsgServerImpl(k Keeper) types.MsgServer {
	return &msgServer{Keeper: k}
}

func (ms msgServer) LockTier(ctx context.Context, msg *types.MsgLockTier) (*types.MsgLockTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, msg.Id)
	if err != nil {
```

**File:** x/tieredrewards/keeper/msg_server.go (L470-516)
```go
func (ms msgServer) WithdrawFromTier(ctx context.Context, msg *types.MsgWithdrawFromTier) (*types.MsgWithdrawFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateWithdrawFromTier(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
	if !balances.IsZero() {
		if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
			return nil, err
		}
	}

	if err := ms.deletePosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionWithdrawn{
		Position: pos.Position,
		Amount:   balances,
	}); err != nil {
		return nil, err
	}

	return &types.MsgWithdrawFromTierResponse{
		Amount: balances,
	}, nil
}
```

**File:** x/tieredrewards/keeper/force_exit.go (L1-2)
```go
// Can be deleted after v8 upgrade
package keeper
```

**File:** x/tieredrewards/keeper/keeper.go (L17-22)
```go
// Keeper of the tieredrewards store.
type Keeper struct {
	cdc          codec.BinaryCodec
	storeService storetypes.KVStoreService
	authority    string

```

**File:** x/tieredrewards/keeper/keeper.go (L44-48)
```go
	mintKeeper         types.MintKeeper
	stakingKeeper      types.StakingKeeper
	accountKeeper      types.AccountKeeper
	bankKeeper         types.BankKeeper
	distributionKeeper types.DistributionKeeper
```
