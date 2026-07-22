### Title
`SwapAllowlistExtension` Allowlist Bypassed by Any User via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. Any user can bypass a per-pool swap allowlist by routing through the public router.

---

### Finding Description

The call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

**Step 1 – Router calls pool with itself as `msg.sender`.**

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly. The pool sees `msg.sender = router`. [1](#0-0) 

**Step 2 – Pool forwards `msg.sender` (router) as `sender` to the extension hook.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, binding the router address as `sender`. [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that `sender` and dispatches it to every configured extension. [3](#0-2) 

**Step 3 – Extension checks the router address, not the user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router) and evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. [4](#0-3) 

There are two consequences:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user, including non-allowlisted ones, passes the gate by routing through the public router |
| Router **is not** allowlisted | Legitimately allowlisted users are blocked when they use the router |

The first case is the critical bypass: to enable router-based swaps at all, the pool admin must allowlist the router address. Once the router is allowlisted, the per-user allowlist is entirely ineffective for router-mediated swaps.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled accounts). Any non-allowlisted user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`). The router is a public, permissionless contract. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. The attacker can drain token1 (or token0) from the restricted pool's bins, causing direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who discovers the allowlist restriction can trivially route through the router instead of calling the pool directly. The bypass is one function call away and requires no capital beyond the swap amount.

---

### Recommendation

The extension must gate the **original end-user**, not the intermediary. Two sound approaches:

1. **Decode the real sender from `extensionData`**: Require the router to ABI-encode `msg.sender` (the original caller) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a coordinated change in the router and the extension.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is to restrict who *receives* output tokens, `recipient` is the correct field to gate. For a swap allowlist that restricts who *initiates* a swap, the extension must receive the original initiator's address through a trusted channel (e.g., signed `extensionData` or a dedicated router that appends the caller).

---

### Proof of Concept

```solidity
// Setup: pool admin allowlists the router so router-based swaps work
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// alice is NOT individually allowlisted
// allowedSwapper[pool][alice] == false

// Alice bypasses the allowlist by routing through MetricOmmSimpleRouter
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    recipient:       alice,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// ✓ swap succeeds — extension checked allowedSwapper[pool][router] == true
// ✓ alice received token1 from the restricted pool
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the gate passes. Alice's address is never consulted. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
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
