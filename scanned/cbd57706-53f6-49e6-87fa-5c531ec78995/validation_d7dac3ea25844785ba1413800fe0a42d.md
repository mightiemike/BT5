### Title
`SwapAllowlistExtension` gates the router's address instead of the actual user — any user can bypass a per-user swap allowlist by routing through `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (a natural operational step), every unprivileged user can bypass the individual-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads the first hook argument (`sender`) and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool populates `sender` with `msg.sender` of the `swap` call and forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol L230-240  (swap)
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the hook
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

```solidity
// ExtensionCalling.sol L160-176  (_beforeSwap)
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
         packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
);
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← user-supplied, forwarded unchanged
    );
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Two identities that must be consistent — the identity the guard checks and the identity that economically executes the swap — are decoupled whenever the router is in the call path.

**Exact corrupted value**: `allowedSwapper[pool][router]` is evaluated; `allowedSwapper[pool][user]` is never consulted.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool and allowlists the `MetricOmmSimpleRouter` (a natural operational step: "allow our official router") inadvertently opens the pool to every user who calls the router. Any address not on the individual allowlist can execute swaps — including adversarial traders — on a pool that was intended to be restricted. Depending on pool configuration this enables:

- Unauthorized access to a restricted-liquidity pool, allowing non-KYC'd or non-whitelisted actors to trade.
- Extraction of LP value through swaps that the pool admin explicitly intended to block.

The inverse also holds: if the admin allowlists individual user addresses but not the router, those allowlisted users cannot swap through the router at all (broken core swap functionality for the intended user set).

---

### Likelihood Explanation

- `SwapAllowlistExtension` and `MetricOmmSimpleRouter` are both production periphery contracts intended to be used together.
- Allowlisting the router is the expected operational pattern for any pool that wants to support router-mediated swaps.
- No special privilege or malicious setup is required: any user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router.
- The bypass is unconditional once the router is allowlisted; no timing, oracle, or state precondition is needed.

---

### Recommendation

The extension must be able to identify the **originating user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` against a router registry and fall through to a user-level check**: The extension recognises known router addresses and, when `sender` is a router, requires the extension data to carry a signed or encoded user identity.

3. **Simpler alternative — gate on `recipient` or require direct pool calls for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory or extension level.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1).
2. Pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
   — allowlisting the router as the "trusted" intermediary.
3. Alice (address not in allowlist) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool:       pool,
           recipient:  alice,
           zeroForOne: true,
           amountIn:   1_000e18,
           ...
       }));

Execution trace
───────────────
router.exactInputSingle(...)
  → pool.swap(alice_recipient, true, 1000e18, ..., extensionData)
      msg.sender at pool = router
  → _beforeSwap(router, alice_recipient, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      allowedSwapper[pool][router] == true  ✓  (no revert)
  → swap executes, alice receives output tokens

Result
──────
Alice — not individually allowlisted — successfully swaps on a
pool that was configured to restrict swaps to specific addresses.
allowedSwapper[pool][alice] is never consulted.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
