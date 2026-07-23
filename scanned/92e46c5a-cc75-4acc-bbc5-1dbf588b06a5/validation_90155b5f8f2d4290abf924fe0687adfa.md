### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` argument against a per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the **router's address**, not the actual user's address. A pool admin who allowlists the router (a necessary step to support router-mediated swaps for legitimate users) inadvertently opens the pool to **all** users, completely defeating the per-user allowlist guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value the pool received as `msg.sender` of its own `swap()` call. The pool passes its `msg.sender` directly as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

Therefore the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot use the router at all |
| **Yes** | Every user on-chain can bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows legitimate users to route through `MetricOmmSimpleRouter` and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of counterparties (e.g., institutional traders, KYC'd addresses). LPs deposit into such a pool expecting only authorized counterparties. Once the router is allowlisted (a necessary operational step), any unprivileged user can call `router.exactInputSingle(...)` and swap against the pool's liquidity. This exposes LPs to:

- Unexpected adverse selection from unauthorized counterparties
- Price impact and LP principal loss from unrestricted swap volume
- Complete nullification of the access-control invariant the pool was deployed to enforce

This is a direct loss of LP assets above Sherlock thresholds in pools where the allowlist is the primary protection mechanism.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a routine operational step for any pool that wants to support the standard periphery swap path. The admin has no on-chain signal that doing so opens the pool to all users; the `SwapAllowlistExtension` interface gives no warning. Any pool that (a) deploys `SwapAllowlistExtension` and (b) allowlists the router is fully exposed. The attacker needs only to call the public router with a valid swap.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** (the end user), not the immediate `msg.sender` of the pool. Two sound approaches:

1. **Require the actual user address in `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The pool's `beforeSwap` hook already forwards `extensionData` unchanged.
2. **Check `recipient` instead of `sender`**: for direct swaps the recipient is often the user; however this breaks for multi-hop paths where the recipient is an intermediate contract.
3. **Separate router-aware allowlist**: maintain a second mapping `allowedSwapper[pool][user]` and require the router to forward the originating user address in a standardized `extensionData` field, verified by the extension.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, Alice, true)   // Alice is the only intended swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // necessary for Alice to use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: Bob, ...})
5. Router calls pool.swap(Bob, ...) → msg.sender of pool = router
6. Extension evaluates: allowedSwapper[pool][router] == true → PASSES
7. Bob's swap executes against LP liquidity despite not being on the allowlist.
``` [4](#0-3) [5](#0-4)

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
