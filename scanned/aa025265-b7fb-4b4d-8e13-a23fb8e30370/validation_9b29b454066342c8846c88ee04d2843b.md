### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Making the Allowlist Bypassable via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed to `beforeSwap`, which is the direct `msg.sender` of `pool.swap()`. When any user routes through the public `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards `sender = router` to the extension. The extension then evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. This creates a binary trap identical in structure to the seeded bug: the guard is either completely bypassable (router allowlisted → every user passes) or it silently breaks all router-mediated swaps for legitimately allowlisted users (router not allowlisted → their transactions revert).

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's own `swap()` entry point.

`MetricOmmPool.swap` passes `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
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

The router never forwards the original `msg.sender` (the real user) to the pool. So the pool sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]`.

**Two mutually exclusive failure modes result:**

| Admin configuration | Outcome |
|---|---|
| Router **not** allowlisted | Allowlisted users who call through the router are rejected — core swap path is broken for them |
| Router **allowlisted** | Every user on the network can bypass the per-user allowlist by routing through the public router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**If the router is allowlisted** (the only way to let legitimate users use the router): any unprivileged address calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. The non-allowlisted user executes a swap in a pool that was explicitly configured to block them. For pools designed to restrict trading to KYC'd counterparties or institutional participants, this is a complete allowlist bypass with direct fund-flow consequences (unauthorized users extract value from the pool's liquidity).

**If the router is not allowlisted**: allowlisted users who attempt to use the standard periphery router receive `NotAllowedToSwap`, making the router-mediated swap path permanently unusable for them — a broken core flow.

Both outcomes are contest-relevant: the first is an admin-boundary break via an unprivileged path; the second is broken core swap functionality.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract. No special role or token is required to call `exactInputSingle`. Any user who wants to bypass the allowlist simply routes through the router. The bypass requires zero privileged access and is reachable in a single transaction.

---

### Recommendation

The extension must verify the identity of the **economic actor** (the human or contract that initiated the swap), not the intermediate dispatcher. Two viable approaches:

1. **Pass the original user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`** (if the intent is to gate who receives output): swap the checked field to `recipient`, which the router sets to `params.recipient` (the actual user-supplied address).

3. **Allowlist the router and add a secondary check**: The extension decodes a user address from `extensionData` and verifies it when `sender == router`. This keeps backward compatibility for direct callers.

The simplest correct fix is for the router to encode the originating user address into `extensionData` and for the extension to decode and check it when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (necessary so that allowlisted users can use the router)

Attack:
  attacker = non-allowlisted EOA
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: restrictedPool,
      recipient: attacker,
      zeroForOne: true,
      amountIn: X,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ passes
      → swap executes, attacker receives output tokens

Result:
  Non-allowlisted attacker successfully swaps in a pool
  configured to block them. The per-user allowlist is
  completely bypassed via the public router.
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
