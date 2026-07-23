Let me check how `_beforeSwap` passes the sender to the extension hook.

The complete call chain is now clear. Here is the analysis:

---

### Title
Router-Mediated Swaps Substitute Router Address for Actual Swapper in `SwapAllowlistExtension.beforeSwap` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, the router's address — not the original user's address — is passed as `sender`. Any user who routes through the router is checked against the router's allowlist entry, not their own. If the router is allowlisted (a natural configuration for pools that want to support router-mediated swaps), the per-user allowlist is completely bypassed by any unprivileged caller.

### Finding Description

**Trace:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that `sender` and forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the originating EOA: [4](#0-3) 

Therefore `sender` arriving at `beforeSwap` is always the router address for any router-mediated swap. The hook evaluates `allowedSwapper[pool][router]` — a single boolean — instead of `allowedSwapper[pool][actual_user]`.

**Consequence:** A pool admin who wants to support router-mediated swaps must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` satisfies the check for **every** caller who routes through it, regardless of whether that caller is individually allowlisted. The per-user granularity the extension is designed to enforce is entirely lost.

**Pause aspect of the question is not valid:** `swap()` carries the `whenNotPaused` modifier, which reverts before `_beforeSwap` is ever reached: [5](#0-4) 

A paused pool cannot be swapped through at all; the extension is never invoked in that state. The "paused-flow regression" framing in the question is a red herring.

### Impact Explanation

The `SwapAllowlistExtension` is documented as gating `swap` by swapper address per pool. When the router is involved, the identity it checks is the router's, not the actual swapper's. Any pool that (a) uses this extension to restrict swaps to a curated set of addresses and (b) also allowlists the router to support normal UX is silently open to any unprivileged user. This constitutes broken core functionality of the extension.

### Likelihood Explanation

The router is the standard user-facing entry point for swaps. A pool admin who deploys `SwapAllowlistExtension` to restrict access but also wants users to be able to use the router will naturally allowlist the router — the code gives no indication that doing so collapses the per-user gate. The misconfiguration is easy to make and hard to detect without auditing the identity substitution.

### Recommendation

The router should forward the originating user's address to the pool so extensions can gate on the real actor. One approach: add an optional `payer`/`originator` field to `extensionData` that the router populates with `msg.sender`, and have `SwapAllowlistExtension.beforeSwap` decode and check that field when `sender` is a known router. A cleaner approach is for the pool to accept an explicit `originator` parameter that the router sets to `msg.sender` before calling `pool.swap()`, similar to how Uniswap v4 passes `msgSender` through the unlock/callback boundary.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`; checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully despite not being individually allowlisted.

Direct call by Bob (`pool.swap(...)` directly) would correctly revert because `allowedSwapper[pool][bob] == false`. The bypass is exclusive to the router path.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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
