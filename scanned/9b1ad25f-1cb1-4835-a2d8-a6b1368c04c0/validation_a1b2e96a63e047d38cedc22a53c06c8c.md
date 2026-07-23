### Title
`SwapAllowlistExtension` checks the router address as the swapper instead of the originating user, allowing any unprivileged caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating EOA. If the pool admin allowlists the router (the only way to let any user reach the pool through the router), the allowlist is completely bypassed for every user, including those the pool was designed to exclude.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the originating user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact address against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle()
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← user-supplied bytes; extension never reads them
    );
```

The router stores the originating user's address only in transient storage for the payment callback; it is never forwarded to the pool or to any extension. The extension has no way to recover the real user's identity.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (broken UX) |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

The second branch is the exploitable path. Once the router is allowlisted, `allowedSwapper[pool][router] == true` and the `if`-branch in `beforeSwap` is never entered for any router-mediated swap, regardless of who the originating user is.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool at oracle-anchored prices; the pool's LP providers bear the counterparty exposure they explicitly opted out of. This is a direct loss of the policy guarantee the pool was configured to enforce, with fund-impacting consequences for LP principals whenever the allowlist was intended to exclude adversarial or uninformed flow.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entry point documented and deployed by the protocol. Any pool admin who wants their allowlisted users to be able to use the router must add the router to the allowlist, which is the natural operational action. The bypass requires no special privilege, no flash loan, and no multi-step setup — a single call to `exactInputSingle` from any EOA suffices. Likelihood is high whenever the pool admin has allowlisted the router.

---

### Recommendation

The extension must gate on the originating user, not on the direct caller of `pool.swap()`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should forward the originating user's address in a standardised field of `extensionData` (or a dedicated parameter), so extensions can recover it.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the originating user from `extensionData` when `sender` is a known router, or the protocol should define a standard interface for routers to attest the real user identity on-chain.

A simpler short-term fix is to check both `sender` and a decoded originator from `extensionData`, requiring the router to sign or encode the real user address in a verifiable way before the pool dispatches the hook.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool:      <curated pool>,
         recipient: bob,
         zeroForOne: true,
         amountIn:  X,
         ...
     })

Trace:
  router.exactInputSingle()
    → pool.swap(sender=router, ...)
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives token1 output

Result:
  bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist invariant is broken.
  If alice is later removed from the allowlist, bob can still trade indefinitely
  via the router as long as the router itself remains allowlisted.
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
