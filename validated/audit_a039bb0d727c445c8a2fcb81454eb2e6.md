### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the individual user is allowlisted. Any user can bypass a per-user swap allowlist by routing through the router if the router itself is allowlisted.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — i.e., whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is that the extension evaluates `allowedSwapper[pool][router]` — a single binary flag for the entire router contract — rather than `allowedSwapper[pool][actualUser]`. The per-user allowlist is structurally unreachable for any router-mediated swap.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties). To also support the standard periphery router, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **every Ethereum address** can bypass the per-user restriction by calling any of the router's `exact*` entry points. The allowlist provides zero protection against router-mediated swaps. Unauthorized users can trade against LP positions in a pool that was explicitly configured to exclude them, directly violating the pool admin's curation policy and any downstream compliance or risk-management assumptions tied to that policy.

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no contract deployment. Any user who knows the pool uses a swap allowlist simply calls `MetricOmmSimpleRouter.exactInputSingle` instead of `pool.swap` directly. The router is the standard, documented periphery entry point, so this path is exercised by every normal user of the protocol. The only precondition is that the pool admin has allowlisted the router — which is the only way to make the router usable at all on an allowlisted pool, so the admin is forced into the vulnerable configuration.

---

### Recommendation

Pass the original user's address through the router to the pool, and have the pool forward it to the extension as `sender`. One approach is for the router to encode the real user address in `extensionData` and for the extension to decode and check it. A cleaner approach is to add a `payer`/`originator` field to the swap call that the pool passes to extensions separately from `msg.sender`. At minimum, document that `SwapAllowlistExtension` cannot enforce per-user restrictions when the router is used, and provide a router-aware variant that recovers the originator from a signed payload or trusted forwarder pattern.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: setAllowedToSwap(pool, router, true)       // enable router
  pool admin calls: setAllowedToSwap(pool, alice, true)        // KYC alice
  pool admin does NOT allowlist bob

Attack (bob bypasses allowlist):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)          // msg.sender = router
    → pool calls _beforeSwap(msg.sender=router, ...)
    → extension checks allowedSwapper[pool][router]   // true → passes
    → swap executes for bob despite bob not being allowlisted

Direct call (correctly blocked):
  bob calls pool.swap(recipient, ...)                 // msg.sender = bob
    → pool calls _beforeSwap(msg.sender=bob, ...)
    → extension checks allowedSwapper[pool][bob]      // false → reverts NotAllowedToSwap
```

The `SwapAllowlistExtension` correctly blocks `bob` on a direct pool call but silently passes when `bob` routes through `MetricOmmSimpleRouter`, because the extension sees `sender = router` (allowlisted) rather than `sender = bob` (not allowlisted). [6](#0-5) [7](#0-6)

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
