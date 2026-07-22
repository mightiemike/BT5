### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via the Public Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the end user's address. If the pool admin allowlists the router to enable router-mediated swaps, the allowlist is silently bypassed for every user — including those the admin explicitly excluded.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (the extension caller). `sender` is the first argument the pool passes to the hook, which is `msg.sender` of `pool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

And `_beforeSwap` encodes this as the `sender` argument forwarded to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap(...)` with `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates a two-sided failure:

1. **Allowlisted users cannot use the router.** If the admin allowlists specific user addresses, those users can only call `pool.swap()` directly. Router-mediated swaps revert because the router is not allowlisted.

2. **Allowlisting the router removes the gate entirely.** If the admin allowlists the router address to enable router-mediated swaps for their approved users, every user — including explicitly excluded ones — can bypass the allowlist by routing through the public `MetricOmmSimpleRouter`.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the economic beneficiary explicitly passed by the caller), not `sender` (the payer/intermediary):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

The swap allowlist has no equivalent "economic actor" field to fall back on. The `recipient` field (the address receiving output tokens) is the closest proxy for the end user in a router-mediated swap, but the extension ignores it entirely.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted user can execute swaps against the pool's liquidity, extracting value from LP positions that were provisioned under the assumption of a restricted counterparty set. This is a direct loss of LP principal and fee revenue to unauthorized actors.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. The only precondition is that the pool admin has allowlisted the router address — a natural and expected operational step for any pool that wants to support router-mediated swaps. The bypass is therefore reachable in every realistic production deployment of `SwapAllowlistExtension` that also supports the router.

### Recommendation

The `beforeSwap` hook should check the identity of the economic actor initiating the swap, not the intermediary contract. Two options:

1. **Check `recipient` instead of `sender`** — when the router calls `pool.swap(recipient=user, ...)`, `recipient` is the end user. The allowlist should gate `recipient`:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
       revert IMetricOmmPoolActions.NotAllowedToSwap();
   }
   ```
   This mirrors how `DepositAllowlistExtension` gates `owner` rather than `sender`.

2. **Have the router forward the original caller via `extensionData`** — the router encodes `msg.sender` into `extensionData`, and the extension decodes and checks it. This requires a coordinated change to both the router and the extension.

Option 1 is simpler and consistent with the deposit allowlist design.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
6. `_beforeSwap` passes `sender = router` to the extension.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps against the pool despite being explicitly excluded from the allowlist. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
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
