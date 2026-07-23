### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the direct `msg.sender` of `pool.swap()`. When a user routes through the public `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user's address. If the pool admin allowlists the router (a natural step to let allowlisted users reach the pool through the router), every user of that router — including non-allowlisted ones — bypasses the swap gate entirely.

### Finding Description

**Root cause — wrong identity checked in the hook**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and gates on it: [1](#0-0) 

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of the pool call: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards it verbatim: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so `sender = router`. The extension never sees the actual user.

**Contrast with `DepositAllowlistExtension`**

The deposit allowlist correctly gates on `owner` (the position beneficiary), which is invariant to who pays: [4](#0-3) 

The swap allowlist has no equivalent stable identity — it gates the intermediary, not the trader.

**Bypass path**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and intends to restrict swaps to a whitelist of known traders.
2. Admin allowlists the router (`allowedSwapper[pool][router] = true`) so that whitelisted users can reach the pool through the standard periphery.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInput/exactOutput` → router calls `pool.swap()` → `sender = router` → `allowedSwapper[pool][router]` is `true` → hook passes → swap executes.

The allowlist is fully defeated for all router-mediated swaps.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to known counterparties (e.g., a permissioned institutional pool) becomes open to any user of the public router. Non-allowlisted users can execute swaps, extract LP value at oracle-anchored prices, and drain one-sided bins. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard).

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router — a natural and expected action when the pool is meant to be accessible through the standard periphery. The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. No privileged access, no special setup, and no non-standard tokens are required.

### Recommendation

Gate on the actual user identity rather than the direct caller. Two options:

1. **Check `recipient`** — the address that receives output tokens is the economically relevant party for a swap. Change the check to `allowedSwapper[msg.sender][recipient]`.
2. **Pass the originating user through `extensionData`** — the router encodes the real user address in `extensionData`, and the extension decodes and checks it. This requires a coordinated router + extension design.

The `DepositAllowlistExtension` pattern (gate on the beneficiary, not the payer/caller) should be mirrored here.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true          // admin enables router access
  allowedSwapper[pool][alice]  = true          // alice is individually whitelisted
  allowedSwapper[pool][bob]    = false         // bob is NOT whitelisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=bob, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → passes
    → swap executes, bob receives tokens

Result:
  bob, a non-allowlisted user, successfully swaps on a restricted pool.
  The SwapAllowlistExtension provided zero protection.
```

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
