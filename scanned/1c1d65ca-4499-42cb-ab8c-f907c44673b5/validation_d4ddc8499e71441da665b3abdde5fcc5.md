### Title
SwapAllowlistExtension Gates the Router Address Instead of the Economic Actor, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. A pool admin who allowlists the router (required for any allowlisted user to use the standard periphery) inadvertently grants swap access to every user who routes through it, completely defeating the per-user allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the curated-pool gate as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

`sender` is always `msg.sender` of the `pool.swap()` call:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the economic actor
    recipient,
    ...
);
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`. The actual user (`msg.sender` of the router call) is stored only in transient storage as the payer and is **never forwarded to the pool or the extension**:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`.

A pool admin who wants their allowlisted users to use the standard periphery **must** allowlist the router address. But allowlisting the router grants swap access to **every** user who routes through it, because the extension cannot distinguish between them — the router is a single address and the actual caller identity is lost.

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the pool. If the router is allowlisted — which is the only way for legitimate allowlisted users to use the standard periphery — the gate is completely open to all users. This allows unauthorized parties to trade on pools intended to be restricted (e.g., KYC-gated, institutional-only, or compliance-restricted pools), constituting a direct policy bypass with fund-impacting consequences for pool LPs and the protocol's curation guarantees.

---

### Likelihood Explanation

Pool admins deploying curated pools with `SwapAllowlistExtension` would naturally want their allowlisted users to use the standard `MetricOmmSimpleRouter`. Allowlisting the router is the only mechanism to enable this. The bypass is therefore reachable in any realistic curated-pool deployment where the router is allowlisted, and requires no special privileges — any EOA can call the router.

---

### Recommendation

The extension must gate the **economic actor** (the actual user), not the intermediary. Options:

1. **Pass the actual user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the router's encoding, which introduces its own trust assumptions.
2. **Separate the allowlist key**: Allowlist by `recipient` (the output token destination) rather than `sender`, since the recipient is always the user-controlled address even through the router. This has trade-offs if the recipient is a contract.
3. **Redesign the hook signature**: Add an explicit `payer` or `originator` field to the `beforeSwap` hook that the pool populates from a trusted source (e.g., a verified router registry), so extensions can always gate the correct actor regardless of routing path.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` (the latter is required for `userA` to use the router).
3. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **true** → swap proceeds.
6. `userB` has successfully swapped on a pool they are not allowlisted for.

The same bypass applies to `exactInput` and `exactOutput` multi-hop paths, and to any other contract that is allowlisted as a convenience for legitimate users. [5](#0-4) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
