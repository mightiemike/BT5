### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap on a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the original user. This creates an irresolvable configuration dilemma: if the pool admin allowlists the router so that allowlisted users can reach the pool through the router, every non-allowlisted user gains the same access by calling the same public router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` checks that `sender` value against the per-pool allowlist.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct for the mapping key) and `sender` is whoever called `pool.swap()`.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself the `sender`.**

`exactInputSingle`, `exactInput`, and `exactOutputSingle` all call `pool.swap(...)` with the router as `msg.sender`: [4](#0-3) 

So when any user routes through the router, the extension sees `sender = router_address`, not the original user.

**Step 4 — The dilemma.**

The pool admin faces two equally broken options:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also pass — bypass achieved** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. Allowlisting the router is the natural operational decision; it is not a malicious or unusual action.

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist on any pool that has `SwapAllowlistExtension` configured and the router allowlisted. The allowlist is the pool's primary access-control boundary for swaps. Bypassing it lets unprivileged addresses execute swaps the pool operator explicitly intended to block — a direct admin-boundary break by an unprivileged path. Pools deployed for regulated or permissioned trading (KYC, institutional-only, etc.) lose their access control entirely.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The only prerequisite is that the pool admin allowlists the router — a routine operational step any admin would take to let their allowlisted users access the router. The bypass is therefore reachable by any user as soon as the pool is configured for normal router use.

---

### Recommendation

The extension must check the **original user's identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add an `originator` field to the hook interface**: The pool passes both `sender` (immediate caller) and `originator` (the address the router recorded as the economic actor). The extension checks `originator`.

Until fixed, pools that need a swap allowlist must not allowlist the router, which means allowlisted users cannot use the router — a broken core swap flow.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // so alice can use the router
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender = router
6. _beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap
7. Check: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes successfully, bypassing the allowlist.
``` [5](#0-4) [6](#0-5) [1](#0-0)

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
