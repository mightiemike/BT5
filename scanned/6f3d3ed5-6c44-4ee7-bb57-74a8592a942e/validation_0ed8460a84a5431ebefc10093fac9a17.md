### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the pool's `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly with no end-user forwarding: [4](#0-3) 

The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The actual end user's allowlist status is never consulted.

This is the direct analog of the Panoptic bug: the wrong actor (`router` / intermediary) is checked instead of the economically relevant actor (the end user), exactly as `msg.sender` was used instead of `from`/`owner` in the reference report.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support the standard periphery router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, **every user on the network** can swap through that pool by routing through `MetricOmmSimpleRouter`, regardless of whether they are individually allowlisted. The allowlist is completely defeated. This constitutes a broken core pool functionality (curation policy) with direct fund-impact consequences: disallowed counterparties can drain LP liquidity at oracle prices from a pool that was designed to restrict access.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who configures a swap allowlist and also wants users to be able to use the router (the normal UX path) will naturally allowlist the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to a public function on a deployed periphery contract.

---

### Recommendation

The extension must check the end user's identity, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` forward `msg.sender` as an authenticated `sender` field in `extensionData`, and have the extension decode and verify it. This requires a trust assumption on the router.

2. **Check `sender` against the allowlist at the pool level before calling extensions**: The pool could enforce that `msg.sender == sender` for allowlisted pools, preventing router indirection entirely.

3. **Preferred — router-aware allowlist**: The extension should accept a signed or router-attested end-user identity from `extensionData` and verify it, rather than relying on the raw `sender` argument which is always the immediate pool caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow router-mediated swaps for allowlisted users)
  - Alice (not individually allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true → passes
  - Alice's swap executes despite not being on the allowlist

Result:
  - Alice bypasses the curated pool's access control
  - Any non-allowlisted user can repeat this via the public router
  - The pool admin's curation policy is entirely nullified
```

The existing unit test `test_allowedSwapSucceeds` in `FullMetricExtension.t.sol` only tests direct pool calls (`callers[0]` is allowlisted and calls the pool directly), never exercising the router-mediated path with a non-allowlisted end user. [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
