### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Any User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router address**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of the pool, not the original EOA
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = pool):

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

When this call reaches the pool, `msg.sender` = **router address**. The pool forwards the router address as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users (e.g., KYC'd addresses).
2. Pool admin also allowlists the router address so that their allowlisted users can use the router for convenience.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds for the unprivileged user.

The original user's identity is completely lost at the pool boundary. The extension has no way to recover it from the call arguments.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or regulatory-compliant participants) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The LP positions in the pool are exposed to unrestricted swap flow, which can cause:

- **Direct LP loss**: the pool was designed to trade only against trusted counterparties; unrestricted access exposes LPs to adversarial or uninformed order flow.
- **Policy/compliance failure**: the allowlist is rendered meaningless, defeating the pool admin's curation intent.

This is a **High** impact finding: the core protection mechanism of a curated pool is silently bypassed, and LP principal is at risk from the unrestricted swap flow.

---

### Likelihood Explanation

The likelihood is **Medium-High**:

- The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and supported by the protocol.
- A pool admin who wants their allowlisted users to be able to use the router (rather than requiring direct pool calls) will naturally allowlist the router address — this is the only way to enable router-mediated swaps on an allowlisted pool.
- The admin has no indication from the extension API or documentation that allowlisting the router opens the pool to all users; the `setAllowedToSwap` setter accepts any address without warning.
- The `generate_scanned_questions.py` audit target explicitly flags this exact scenario: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediary router. Two viable approaches:

1. **Pass original user via `extensionData`**: The router encodes `msg.sender` (original EOA) into `extensionData`; the extension decodes and verifies it. This requires the extension to trust that the router correctly encodes the original user, which introduces a trust assumption on the router.

2. **Check `sender` only when it is not a known router**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the original user from `extensionData`; otherwise it checks `sender` directly.

3. **Preferred — gate `sender` and require direct pool calls for allowlisted pools**: Document clearly that allowlisted pools must not allowlist the router, and that allowlisted users must call the pool directly. Add a NatSpec warning to `setAllowedToSwap`.

---

### Proof of Concept

```solidity
// Scenario: pool admin allowlists userA and the router; attacker (userB) bypasses the allowlist

// 1. Pool admin setup
swapAllowlist.setAllowedToSwap(pool, userA, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true); // to let userA use the router

// 2. Attacker (userB, NOT allowlisted) calls the router
vm.startPrank(userB);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    recipient: userB,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ↑ succeeds — extension sees sender=router (allowlisted), not userB (not allowlisted)
vm.stopPrank();
```

The swap succeeds for `userB` because the extension checks `allowedSwapper[pool][router]` (true), not `allowedSwapper[pool][userB]` (false). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
