### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` of its own `swap()` call as `sender`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the **router's address**, not the actual user's address. If the pool admin allowlists the router (the natural step to let their intended users reach the pool via the router), every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router] → true → passes
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user-identity forwarding: [4](#0-3) 

The pool admin has no mechanism to simultaneously:
1. Allowlist the router so their intended users can reach the pool via the router.
2. Block non-allowlisted users from doing the same.

Allowlisting the router collapses the allowlist to "anyone who calls the router," which is every user.

The existing integration test confirms the allowlist is keyed to the **direct caller** of `pool.swap()` (a `TestCaller` wrapper), not the end-user EOA: [5](#0-4) 

No test exercises the router-against-allowlisted-pool path, leaving the bypass undetected.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., approved market makers, KYC'd addresses, or protocol-internal actors) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted, the curation boundary is gone: unauthorized traders can execute swaps, causing adverse selection against LPs, violating the pool's intended access policy, and potentially draining value from a pool whose LP positions were sized under the assumption that only vetted counterparties would trade. This is an admin-boundary break where an unprivileged path defeats a configured guard.

---

### Likelihood Explanation

The scenario is realistic. A pool admin who deploys a curated pool and wants their allowlisted users to access it via the standard router will naturally call `setAllowedToSwap(pool, router, true)`. Nothing in the interface, NatSpec, or documentation warns that this collapses the allowlist for all users. The bypass requires only that the pool admin takes this one intuitive configuration step; no privileged attacker capability is needed beyond calling the public router.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `sender` only for direct pool calls; require a signed or verified identity for router calls**: The extension inspects whether `sender` is a known router and, if so, reads the real user from a verified field in `extensionData`.
3. **Document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly**: This is the minimal mitigation but breaks router UX for curated pools.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedMM` is allowlisted.
swapExt.setAllowedToSwap(pool, trustedMM, true);
// Pool admin also allowlists the router so trustedMM can use it:
swapExt.setAllowedToSwap(pool, address(router), true);

// Attack: attacker (not allowlisted) routes through the public router.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Extension sees sender = router → allowedSwapper[pool][router] = true → passes.
// Attacker swaps successfully despite not being on the allowlist.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
