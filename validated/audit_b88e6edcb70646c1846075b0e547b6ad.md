### Title
SwapAllowlistExtension Checks Router Address Instead of End-User Address, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards that as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. A pool admin who allowlists the router to make router-mediated swaps work for any user inadvertently opens the gate to every address on-chain, completely defeating the allowlist.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the pool sees `msg.sender = router`.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, i.e., `_beforeSwap(router, ...)`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the configured extension.
5. `SwapAllowlistExtension.beforeSwap(address sender, ...)` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — never the end user: [3](#0-2) 

**Two broken outcomes result from this identity mismatch:**

**Outcome A — Allowlist bypass (High impact):** A pool admin allowlists specific users (e.g., `alice`, `bob`) but discovers that their swaps through the router revert because the router is not in the allowlist. To fix this, the admin adds the router to the allowlist. From that point, *any* address can bypass the per-user gate by routing through `MetricOmmSimpleRouter`, because the extension only sees `allowedSwapper[pool][router] = true`.

**Outcome B — Broken core functionality (Medium impact):** If the admin does not allowlist the router, every allowlisted user's swap through the router reverts with `NotAllowedToSwap`, even though they are individually permitted. The router — the primary user-facing entry point — is unusable for any allowlisted pool.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed to `addLiquidity`), not on `sender`: [4](#0-3) 

---

### Impact Explanation

**Outcome A** is a direct allowlist bypass: a pool configured for restricted access (e.g., KYC-only, institutional-only) can be traded by any unprivileged address once the admin takes the natural remediation step of allowlisting the router. This breaks the core security invariant the extension is designed to enforce and constitutes broken core pool functionality with fund-impacting consequences (unauthorized parties execute swaps against a pool that should reject them).

**Outcome B** renders the router unusable for any pool with `SwapAllowlistExtension` active, breaking the primary swap path for all allowlisted users.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical user-facing swap entry point; virtually all end-user swaps go through it.
- Any pool that deploys `SwapAllowlistExtension` and expects router-mediated swaps to work will encounter Outcome B immediately, and the natural remediation (allowlisting the router) produces Outcome A.
- No privileged attacker role is required; any address can call the router.
- The admin action that triggers the bypass (allowlisting the router) is the expected fix for the broken behavior, making it highly likely in practice.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the *actual end user*, not the intermediary router. Two approaches:

1. **Pass the real initiator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.

2. **Check `recipient` instead of `sender` (if recipient == end user):** Only viable when the pool's swap is called with `recipient = actual user`, which is the common case in `exactInputSingle`.

3. **Preferred — mirror the Uniswap v3 pattern:** Have the router store the real payer/initiator in transient storage (as it already does for callback context via `_setNextCallbackContext`) and expose a view that the extension can read. The extension then checks the transient-stored initiator rather than the `sender` argument.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin allowlists alice: setAllowedToSwap(pool, alice, true)

Step 1 — Confirm alice cannot swap via router (Outcome B):
  vm.prank(alice);
  router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
  // REVERTS: NotAllowedToSwap — extension sees sender=router, not alice

Step 2 — Admin "fixes" by allowlisting the router:
  vm.prank(admin);
  extension.setAllowedToSwap(pool, address(router), true);

Step 3 — Attacker (not allowlisted) bypasses the gate (Outcome A):
  vm.prank(attacker);  // attacker is NOT in allowedSwapper
  router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
  // SUCCEEDS: extension sees sender=router, allowedSwapper[pool][router]=true
  // Allowlist is completely bypassed
``` [5](#0-4) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
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
