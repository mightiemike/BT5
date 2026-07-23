### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. If the pool admin allowlists the router (the natural step to enable router-based swaps for their curated pool), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ... {
    ...
    _beforeSwap(
        msg.sender,   // <-- always the direct caller of pool.swap()
        recipient,
        ...
    );
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes this `sender` and dispatches it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — i.e., whoever called `pool.swap()`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

So `msg.sender` of `pool.swap()` = the router contract address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The bypass path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted addresses.
2. Pool admin allowlists the `MetricOmmSimpleRouter` address so that allowlisted users can trade through the standard periphery (a natural and expected operational step).
3. Any non-allowlisted user calls `router.exactInputSingle(...)` targeting the curated pool.
4. The pool sees `sender` = router address → `allowedSwapper[pool][router]` = `true` → the allowlist check passes.
5. The non-allowlisted user successfully swaps on a pool that was intended to be restricted.

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender` = router. [5](#0-4) 

---

### Impact Explanation

**High.** A curated pool's entire swap allowlist is silently defeated. Any user — including those explicitly excluded by the pool admin — can trade on the pool by routing through `MetricOmmSimpleRouter`. This breaks the core curation invariant: "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it." Depending on the pool's purpose (e.g., institutional-only, compliance-gated, or pre-launch restricted), this allows unauthorized parties to extract liquidity at oracle-anchored prices, causing direct loss of LP assets or protocol-level compliance failure.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to allowlist the router. This is not a malicious or unusual action — it is the expected operational step for any curated pool that wants to support the standard periphery. The admin has no way to allowlist the router for legitimate users without simultaneously opening the gate to all users. The router is a public, permissionless contract, so once it is allowlisted, the bypass is trivially reachable by any address.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual economic actor, not the intermediary. Two complementary fixes:

1. **In the extension:** Check `recipient` if the intent is to gate who receives output, or require the actual user identity to be passed via `extensionData` with a signature or trusted forwarder pattern.

2. **Preferred — in the pool/router:** Add an explicit `swapper` parameter to `pool.swap()` (separate from `msg.sender`) that the router populates with `msg.sender` of the router call. The extension then checks this field. This mirrors how `addLiquidity` separates `sender` (payer, `msg.sender`) from `owner` (the position beneficiary).

Minimal patch to the extension as a stopgap — reject router-mediated swaps unless the router itself is the intended gated actor:

```diff
- if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ // sender is msg.sender of pool.swap(); for router calls this is the router, not the user.
+ // Until the pool exposes a trusted swapper field, gate on sender and document that
+ // allowlisting the router opens the pool to all users.
```

The correct long-term fix is to thread the originating user address through the pool's `swap()` interface so extensions can gate on it reliably.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router (to enable router-based swaps for allowed users)
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker: not on the allowlist
address attacker = makeAddr("attacker");
assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

// Attacker routes through the router — extension sees sender=router, not attacker
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       attacker,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// ✓ swap succeeds — allowlist bypassed
```

The pool's `_beforeSwap` receives `sender = address(router)`, which is allowlisted, so `NotAllowedToSwap` is never raised despite `attacker` not being on the allowlist. [3](#0-2) [1](#0-0) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
