I now have all the information needed. Let me trace the exact call path for the swap allowlist bypass.

### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the **immediate caller of `pool.swap()`** against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, that immediate caller is the router contract, not the end user. If the pool admin allowlists the router address (the only way to let allowlisted users trade through the router), every non-allowlisted address can bypass the curation gate by routing through the same public router.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ...) [msg.sender = router]
               └─ _beforeSwap(msg.sender=router, recipient, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  ← router is checked, not user
```

`MetricOmmPool.swap()` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value verbatim to the extension:

```solidity
// ExtensionCalling.sol line 162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap()` then evaluates:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**The forced dilemma for the pool admin:**

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router; only direct `pool.swap()` calls work |
| **Allowlists the router** | Every non-allowlisted address can bypass the gate by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The design of the system forces the admin into one of these two broken states.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses). Once the router is allowlisted:

- Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle()` / `exactInput()` / `exactOutputSingle()` / `exactOutput()` and trade against the pool's LP reserves.
- LP providers who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted adversarial flow, including MEV, front-running, and directional attacks.
- This constitutes a direct loss of LP principal through bad-price execution that the configured protection was supposed to prevent.

**Severity: High** — broken core allowlist invariant with direct LP fund impact, reachable by any unprivileged address once the router is allowlisted.

---

### Likelihood Explanation

The router is the canonical, documented entry point for end-user swaps. A pool admin who deploys a curated pool and wants allowlisted users to trade normally (not via raw `pool.swap()` calls) will inevitably allowlist the router. The bypass is then immediately available to every address on-chain. No privileged key, no special setup, and no malicious initial configuration is required beyond the admin making the natural operational choice.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor** (the end user), not the transport layer (the router). Two viable approaches:

1. **Extension-data forwarding**: Require the router to embed the originating user's address in `extensionData`; the extension decodes and checks that address. The pool admin must also configure the extension to trust the router as a forwarding intermediary.

2. **Separate `originalSender` parameter**: Add an `originalSender` field to the `beforeSwap` hook signature that the pool populates from a trusted transient-storage slot set by the router before calling `pool.swap()`, analogous to how the router already stores the payer in `_setNextCallbackContext`.

Until fixed, pool admins should be warned that allowlisting the router on a `SwapAllowlistExtension`-guarded pool removes all swap-side curation.

---

### Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists the router so that allowlisted users can trade via router
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Attacker (not individually allowlisted) calls the router
// router.exactInputSingle() → pool.swap(msg.sender=router) → beforeSwap(sender=router)
// allowedSwapper[pool][router] == true  → check passes → swap executes
router.exactInputSingle(ExactInputSingleParams({
    pool:            address(pool),
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        largeAmount,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    tokenIn:         token0,
    extensionData:   ""
}));
// Non-allowlisted attacker successfully drains LP reserves
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
