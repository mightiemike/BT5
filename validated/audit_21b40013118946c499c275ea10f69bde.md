### Title
SwapAllowlistExtension gates the router address instead of the actual end-user, making the per-user swap allowlist bypassable through MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the individual swapper is allowlisted. If the pool admin allowlists the router (a necessary step to permit any router-mediated swap for allowlisted users), every unprivileged user can bypass the per-user gate by routing through the router.

---

### Finding Description

**Identity mismatch in the allowlist check**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the value the pool forwarded — the immediate caller of `pool.swap()`.

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
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

`msg.sender` to the pool is the **router contract address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The irreconcilable bind**

The pool admin faces two mutually exclusive bad outcomes:

| Router allowlist state | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Router **not** allowlisted | Must call `pool.swap()` directly; router unusable | Correctly blocked |
| Router **allowlisted** | Can use router | **Also** pass the check — allowlist bypassed |

There is no configuration that allows allowlisted users to use the router while blocking non-allowlisted users. The checked identity (router) is structurally decoupled from the intended identity (end user).

This is the direct analog to the MarginFi `marginfi_account_idx` bug: in MarginFi the wrong account index is validated (allowing a new account to be substituted for an existing one); here the wrong address is validated (allowing any user to substitute the router's allowlisted identity for their own non-allowlisted one).

---

### Impact Explanation

A non-allowlisted user can execute swaps on a pool whose admin deployed `SwapAllowlistExtension` to restrict access. The bypass requires only that the pool admin has allowlisted the router — a configuration the admin must make if they want any allowlisted user to be able to use the router. Once the router is allowlisted, the restriction is lifted for the entire public. Unauthorized swaps can drain liquidity, cause price impact, or extract value from a pool that was intended to be private or semi-private. This breaks the core access-control invariant of the pool.

---

### Likelihood Explanation

Medium. The bypass is only reachable when the pool admin has allowlisted the router. However, this is a natural and expected configuration: any pool admin who wants allowlisted users to be able to use the standard periphery router must allowlist it. The admin has no way to know that doing so opens the gate to everyone. The router is a public, factory-verified contract, so allowlisting it appears safe on its face.

---

### Recommendation

The extension must check the **actual end-user identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: Require the router to ABI-encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. The extension must reject calls where `extensionData` is absent or malformed.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the real user. This is imperfect for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router wrapper**: Deploy a thin allowlist-aware router that checks the caller before forwarding to the pool, and allowlist only that wrapper. The wrapper enforces the per-user check before calling `pool.swap()`.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData` and the extension verifies it, making the checked identity tamper-evident and tied to the actual user.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: charlie, ...})

  router calls:
    pool.swap(recipient=charlie, ...) with msg.sender = router

  pool calls extension:
    extension.beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  charlie swaps successfully on a pool he is not allowlisted for.
  The per-user allowlist is fully bypassed.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
