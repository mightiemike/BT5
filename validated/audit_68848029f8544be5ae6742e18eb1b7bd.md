### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router**, not the original user. If the pool admin allowlists the router (the natural operational choice to let users use the official periphery), every unprivileged user can bypass the swap allowlist by routing through it.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:** [1](#0-0) 

The extension receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**What the pool passes as `sender`:** [2](#0-1) 

The pool always passes `msg.sender` — the immediate caller of `pool.swap()` — as the `sender` argument to `_beforeSwap`, and therefore to every configured extension.

**What the router passes to the pool:** [3](#0-2) 

`exactInputSingle` calls `pool.swap(...)` directly. Inside the pool, `msg.sender` is the **router contract**, not the original EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same substitution occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**The bypass path:**

A pool admin who wants to allow users to swap through the official router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the extension's check passes for **every caller** of the router, regardless of whether that caller is individually authorized. The allowlist is effectively nullified for all router-mediated swaps.

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards the wrong actor: [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-approved addresses, specific market makers, or whitelisted counterparties loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The unauthorized user receives real token output from the pool's LP reserves; LPs suffer the economic consequence of trades they were never meant to settle. This is a direct loss of the pool's curation guarantee and constitutes a broken core pool functionality with fund-impacting consequences above Sherlock Medium/High thresholds.

---

### Likelihood Explanation

The trigger requires only two conditions, both of which are the natural operational state:

1. A pool is deployed with `SwapAllowlistExtension` configured on `beforeSwap`.
2. The pool admin allowlists the router so that authorized users can use the standard periphery.

No privileged action by the attacker is needed. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the curated pool and the swap will succeed.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **economically relevant actor**, not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address instead of `sender`.
2. **Check `recipient` or a dedicated field**: Alternatively, add an `originalSender` field to the extension ABI that the router always populates, and have the extension verify that field against the allowlist.

The simplest safe fix is to have the router encode the original `msg.sender` into `extensionData` and have the extension decode and check it, so the allowlist always gates the address that initiated the transaction.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension on beforeSwap
  - Admin calls setAllowedToSwap(pool, router, true)   // allow official router
  - Admin does NOT allowlist attacker (0xBad)

Attack:
  1. 0xBad calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  3. Pool calls _beforeSwap(router, recipient, ...)
  4. ExtensionCalling encodes sender = router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks allowedSwapper[pool][router] == true  → passes
  6. Swap executes; 0xBad receives token output from LP reserves

Result:
  0xBad successfully swaps on a pool that was supposed to block them.
  Every LP in the pool bears the cost of an unauthorized trade.
``` [6](#0-5) [2](#0-1) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
