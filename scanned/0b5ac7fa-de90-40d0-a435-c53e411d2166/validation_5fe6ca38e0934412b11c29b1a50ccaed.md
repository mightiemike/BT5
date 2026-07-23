### Title
`SwapAllowlistExtension` Gates by Router `sender` Instead of Actual User, Allowing Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces the swap allowlist by checking the `sender` parameter, which is the **direct caller of the pool** (i.e., `MetricOmmSimpleRouter`), not the end-user initiating the trade. Because the router must be allowlisted for the pool to be usable through the periphery, any unprivileged user can route through the allowlisted router and bypass the per-user swap gate entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives two address parameters from the pool: `sender` (the direct `msg.sender` of the pool's `swap()` call) and `recipient` (who receives output tokens). The extension ignores `recipient` and checks only `sender`:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`. The pool then calls `_beforeSwap(sender = router_address, recipient = user_address, ...)`:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(
    address sender,   // ← pool's msg.sender = router
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap,
            (sender, recipient, ...))
    );
}
``` [2](#0-1) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. For the router to function at all with this pool, the pool admin must allowlist the router address. Once the router is allowlisted, the check passes for **every user** who routes through it, regardless of whether that user is individually allowlisted.

This is structurally identical to the StakerVault analog: just as a strategy could transfer tokens to a non-strategy to sidestep the reward guard (because the guard checked the holder's classification, not the transfer path), here a non-allowlisted user sidesteps the swap guard by routing through an allowlisted intermediary (because the guard checks the intermediary's address, not the user's).

Contrast with `DepositAllowlistExtension`, which correctly checks `owner` (the LP position beneficiary) rather than `sender` (the router):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The asymmetry confirms the design intent: the deposit allowlist gates by the **beneficiary** (`owner`), so the swap allowlist should gate by the **actual user** — but it gates by the **router** instead.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) provides **zero enforcement** once the router is allowlisted. Any address can execute swaps against the restricted pool by calling `MetricOmmSimpleRouter`, draining liquidity or executing trades that the pool admin explicitly intended to block. The allowlist guard is rendered a no-op for all router-mediated swaps.

---

### Likelihood Explanation

The scenario requires:
1. A pool configured with `SwapAllowlistExtension` (a production extension, not a mock).
2. The pool admin allowlisting `MetricOmmSimpleRouter` so that normal users can interact via the periphery.

Both conditions are the expected production configuration for any permissioned pool that still wants to support the standard router. The bypass is therefore reachable by any user in the normal interaction flow.

---

### Recommendation

Gate by the actual user, not the intermediary. The `beforeSwap` hook receives `recipient` as its second parameter. For a swap allowlist, the meaningful identity to check is the address that **initiates and benefits from** the swap. Two options:

**Option 1 — Check `recipient` instead of `sender`:**
```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**Option 2 — Check both `sender` and `recipient`** (require both to be allowlisted, or require at least one):
```solidity
if (!allowAllSwappers[msg.sender]
    && !allowedSwapper[msg.sender][sender]
    && !allowedSwapper[msg.sender][recipient]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

The correct choice depends on the pool admin's intent. If the goal is to gate who receives tokens (the economic beneficiary), check `recipient`. If the goal is to gate the initiating contract, check `sender`. Document the distinction clearly so pool admins configure the allowlist for the right address.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only `[alice, router]` as swappers (router must be allowlisted for normal operation).
2. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)` with `recipient = bob`.
3. The router calls `pool.swap(...)`. The pool calls `_beforeSwap(sender = router, recipient = bob, ...)`.
4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
5. Bob's swap executes successfully against the restricted pool, bypassing the allowlist entirely.
6. The pool admin's intent — that only `alice` may swap — is violated with no on-chain mechanism to prevent it.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
