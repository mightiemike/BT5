### Title
SwapAllowlistExtension Allowlist Keyed on `sender` (Router Address) Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `beforeSwap` extension hook receives `sender = msg.sender` as seen by the pool. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract address**, not the originating user. The `SwapAllowlistExtension` allowlist is keyed by `(pool, sender)`. Because every user who routes through the router shares the same `sender` value (the router address), the allowlist key is non-unique across users: allowlisting the router grants access to all users indiscriminately, and allowlisting individual users blocks them when they use the router. Either configuration breaks the intended access control.

---

### Finding Description

**Root cause — non-unique identity key in the allowlist mapping:**

In `MetricOmmPool.swap()`, the `_beforeSwap` dispatcher is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly on behalf of the user: [2](#0-1) 

So the pool's `msg.sender` — and therefore the `sender` argument forwarded to every `beforeSwap` extension — is the **router's address**, not the originating EOA. The same is true for `exactInput`, `exactOutputSingle`, and `exactOutput`.

The `SwapAllowlistExtension.beforeSwap()` performs an `allowedSwapper` lookup keyed by `(pool, sender)`:

> *"allowAll/allowedSwapper lookup keyed by pool and sender"* — `generate_scanned_questions.py` target description [3](#0-2) 

Because `sender` is the router address for every user who routes through `MetricOmmSimpleRouter`, the key is **non-unique across users** — an exact structural analog to the LineaRollup `dataHash` cardinality bug where a shared key causes the guard to be misapplied.

**Two exploitable configurations arise from this:**

**Config A — Router is allowlisted (to permit router-mediated swaps):**
Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()`. The pool sees `sender = router`. The allowlist lookup finds the router is permitted and the swap proceeds. The allowlist is completely bypassed for all users.

**Config B — Individual users are allowlisted (intended design):**
An allowlisted user calls `exactInputSingle()`. The pool sees `sender = router`. The router is not in the allowlist. The swap reverts with `NotAllowedToSwap`. Legitimate allowlisted users cannot use the standard periphery router at all.

Both configurations are reachable by any unprivileged user with no special setup.

---

### Impact Explanation

- **Config A**: Any user bypasses a swap allowlist on a restricted pool. Unauthorized parties can execute swaps, drain liquidity at oracle prices, or front-run restricted LPs. Direct loss of user principal and protocol fees.
- **Config B**: Allowlisted users are locked out of the standard swap path. Core swap functionality is broken for the intended participants. Funds are effectively frozen in the pool from the perspective of the allowlisted users.

Both impacts are above Sherlock Medium/High thresholds: Config A is a broken access-control invariant with direct fund impact; Config B is broken core pool functionality.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, public, documented swap entry point for the protocol.
- Any user can call it without any special privilege.
- The bug is triggered on every router-mediated swap to a pool with `SwapAllowlistExtension` configured.
- No admin action, malicious setup, or non-standard token is required.

Likelihood: **High**.

---

### Recommendation

The allowlist must gate the **economically relevant actor** — the originating user — not the intermediary router. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension reads and verifies this value. This requires the extension to trust the router, which must be separately enforced.

2. **Check `recipient` instead of `sender`** (for exact-input swaps where `recipient` is the user): This is swap-direction-dependent and does not generalize cleanly.

3. **Preferred — use a dedicated allowlist entry point**: Require allowlisted users to interact directly with the pool (not through the router), or deploy a router variant that is itself the gated entry point and enforces its own allowlist before calling the pool.

The core invariant to enforce: the identity checked by the extension must uniquely identify the originating user, not a shared intermediary whose address is the same for all callers.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order set)
  allowlist.setAllowedSwapper(pool, router_address, true)   // Config A: router allowlisted
  allowlist.setAllowedSwapper(pool, alice, false)           // alice NOT individually listed

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=alice, ...)
    → pool calls _beforeSwap(sender=router_address, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router_address] == true
    → hook returns selector (no revert)
    → swap executes for alice despite alice not being allowlisted

Result:
  alice successfully swaps in a pool she is not authorized to access.
  The allowlist provides zero protection for any user who routes through the router.
``` [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
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
