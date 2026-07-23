### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool sees the **router** as `sender`, not the actual end-user. Any pool admin who allowlists the router to enable router-based swaps inadvertently grants every user of that router unrestricted swap access, completely defeating the allowlist.

---

### Finding Description

**Call chain for a router swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, zeroForOne, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` here is the pool (correct), and `sender` is the router. So the check is `allowedSwapper[pool][router]`.

For router-based swaps to work at all on an allowlisted pool, the pool admin **must** add the router to the allowlist. Once the router is allowlisted, every user who calls `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` passes the check — regardless of whether that individual user is permitted.

The `MetricOmmSimpleRouter` does not forward the original `msg.sender` (the actual user) to the pool in any way that the extension can inspect. The pool's `swap` interface has no `originator` field; the only identity the extension receives is `sender = router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified traders, protocol-owned bots, or whitelisted market makers) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The unauthorized user can:

- Execute swaps at oracle-derived prices against LP capital that was deposited under the assumption of a restricted counterparty set.
- Drain bins of one token at the oracle bid/ask, causing LP losses if the oracle is stale or the pool is intentionally illiquid for non-whitelisted parties.
- Violate compliance or access-control invariants the pool admin relied upon.

This is a broken core pool functionality: the allowlist guard is rendered completely ineffective for the primary periphery swap path.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical, publicly deployed swap entry point for the protocol.
- Any pool that enables router-based swaps must allowlist the router, which is the normal operational setup.
- No special knowledge or privileged access is required — any user can call the router.
- The bypass is unconditional once the router is allowlisted; there is no per-call mitigation.

---

### Recommendation

The extension must gate on the **actual end-user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the pool interface.** Add an `originator` field to the swap call or extension data so the router can forward `msg.sender` and the extension can verify it. This requires a coordinated interface change.

2. **Short-term: check `sender` AND `recipient` or require direct pool calls only.** As a stopgap, the extension can reject calls where `sender` is a known router unless the pool admin explicitly opts into router-mediated access. However, this is fragile.

3. **Preferred: gate by `recipient` for swap allowlists, or require the extension to decode an authenticated originator from `extensionData`.** The router already forwards `extensionData` unchanged, so the user could sign an allowlist proof that the extension verifies — but this requires off-chain infrastructure.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists only `trustedTrader` and the router (to enable router swaps).
allowlistExt.setAllowedToSwap(pool, address(router), true);
allowlistExt.setAllowedToSwap(pool, trustedTrader, true);

// Attacker (not allowlisted) bypasses the guard via the router:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            pool,
        tokenIn:         token0,
        recipient:       attacker,
        zeroForOne:      true,
        amountIn:        1_000e6,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
// Succeeds: pool.swap sees sender=router, extension checks allowedSwapper[pool][router]=true.
// Attacker swaps against restricted LP capital without being on the allowlist.
```

**Root cause location:** [1](#0-0) 

**Pool passes `msg.sender` (router) as `sender`:** [2](#0-1) 

**`ExtensionCalling._beforeSwap` forwards it unchanged:** [3](#0-2) 

**Router calls `pool.swap` with itself as `msg.sender`:** [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
