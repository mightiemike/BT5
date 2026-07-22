### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the **direct caller of `pool.swap()`**. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every unprivileged user can bypass the allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes `sender` (the direct pool caller) into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the router calls `pool.swap()` directly, making `msg.sender` to the pool — and therefore `sender` to the extension — the **router address**, not the end user: [4](#0-3) 

The end user's identity is stored only in transient callback context (`_setNextCallbackContext`) and is never surfaced to the extension layer. The extension has no mechanism to recover it.

**Bypass path:**

1. Pool admin configures `SwapAllowlistExtension` to restrict swaps to a set of approved addresses.
2. Pool admin also adds `allowedSwapper[pool][router] = true` so that approved users can reach the pool through the router.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(...)` — `sender` seen by the extension is the router.
5. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The non-allowlisted user's swap executes without restriction.

The pool admin has no way to simultaneously allow allowlisted users to use the router **and** block non-allowlisted users from using the router, because the extension cannot distinguish between the two once the router is the direct caller.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for curated/private pools. Its bypass constitutes an **admin-boundary break**: an unprivileged path (the public router) circumvents the access control the pool admin configured. Any user can trade on a pool that was intended to be restricted, violating the pool's curation invariant. Depending on the pool's purpose (e.g., institutional-only, regulatory-restricted, or oracle-sensitive private pools), this can expose LPs to adverse selection from actors the admin explicitly excluded.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the standard public entry point for swaps. Any pool admin who allowlists the router to support router-mediated swaps for their approved users inadvertently opens the pool to all users. The attacker needs no special privileges — only the ability to call the public router.

---

### Recommendation

The extension must gate on the **end user's identity**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Extension-data identity forwarding**: Require the router to encode the originating user in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that identity when `sender` is a known router. This requires a coordinated interface change between the router and the extension.

2. **Separate router allowlist from user allowlist**: Document clearly that allowlisting the router is equivalent to `allowAllSwappers = true`, and provide a separate mechanism (e.g., a trusted forwarder pattern) that lets the router attest the end user's identity on-chain before the extension check.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin has set:
//   allowedSwapper[pool][alice]  = true   (alice is approved)
//   allowedSwapper[pool][router] = true   (router needed for alice to use it)

// Bob (not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    recipient:       bob,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    tokenIn:         token0,
    extensionData:   ""
}));

// Inside pool.swap():
//   sender = address(router)
// Inside SwapAllowlistExtension.beforeSwap():
//   allowedSwapper[pool][router] == true  → passes
// Bob's swap executes despite not being on the allowlist.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
