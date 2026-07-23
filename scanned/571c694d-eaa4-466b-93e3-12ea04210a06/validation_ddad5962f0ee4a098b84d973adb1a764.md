### Title
Router-Mediated Swaps Check Router Identity Instead of End-User Identity in SwapAllowlistExtension — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension::beforeSwap` hook gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap` function receives `msg.sender = router`, so the hook checks whether the **router** is allowlisted — not the original end-user. This makes the allowlist extension structurally incompatible with the router: allowlisting the router opens the gate to every user, and not allowlisting it blocks every allowlisted user from using the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter::exactInputSingle(params)
         └─ pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              │  msg.sender = router
              └─ _beforeSwap(msg.sender=router, ...)
                   └─ extension.beforeSwap(sender=router, ...)
                        └─ allowedSwapper[msg.sender=pool][sender=router]  ← checks router, not user
```

In `MetricOmmPool::swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that `sender` directly to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router never injects the original caller's address into the pool call — it simply calls `pool.swap(...)` directly: [4](#0-3) 

---

### Impact Explanation

Two broken outcomes arise:

**Outcome A — Allowlist bypass (high impact):** A pool admin allowlists specific users (e.g., KYC-only addresses) and also allowlists the router so that those users can trade via the router. Because the hook checks `allowedSwapper[pool][router]`, any unprivileged user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and pass the hook — the individual allowlist is completely bypassed.

**Outcome B — Allowlisted users locked out of router (medium impact):** If the admin does not allowlist the router, every allowlisted user is silently blocked from using the router even though they are individually permitted. Core swap functionality is broken for the intended user set.

Both outcomes violate the invariant stated in the extension's own NatSpec: *"Gates `swap` by swapper address, per pool."* [5](#0-4) 

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the canonical router is affected. The router is the standard public entry point for multi-hop and single-hop swaps. No special privileges, timing, or oracle manipulation are required — a single `exactInputSingle` call from any EOA is sufficient to trigger Outcome A.

---

### Note on Question Framing

The question's framing — "timed-threshold manipulation," "stale threshold state," "two public transactions in sequence," and "remove-liquidity calls while the pool is paused" — does not match the actual code. There is no time-based threshold, no observation accumulator, and no stale state in `SwapAllowlistExtension`. The real issue is a straightforward, single-transaction identity mismatch between the router's `msg.sender` and the intended end-user address. The "two transactions" and "paused pool" elements are irrelevant to this finding.

---

### Recommendation

The extension should not rely solely on the `sender` argument (which is the immediate caller of `pool.swap`). Options:

1. **Pass the true originator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension verifies it against a signed or trusted field. This requires router cooperation and is fragile.
2. **Allowlist at the router level, not the extension level**: Gate router access separately and remove the per-user allowlist from the extension when router usage is expected.
3. **Reject router-mediated swaps explicitly**: Document that `SwapAllowlistExtension` is incompatible with the router and revert if `sender` is a known router address.
4. **Preferred — propagate originator in the hook interface**: The pool could pass both `msg.sender` (immediate caller) and an authenticated originator, letting extensions choose which identity to gate.

---

### Proof of Concept

```solidity
// Setup: pool uses SwapAllowlistExtension; only `alice` is allowlisted
extension.setAllowedToSwap(pool, alice, true);
// Admin also allowlists the router so alice can trade via it:
extension.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) calls the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: bob,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Hook checks allowedSwapper[pool][router] == true → passes
// Bob successfully swaps on a pool he was never allowlisted for
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
