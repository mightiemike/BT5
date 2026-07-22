Now I have all the code I need. Let me trace the exact call chain.

The call chain is now fully traced. Here is the determination:

---

### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User on All Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`. For every router-mediated swap — including every hop in a multi-hop `exactOutput` — `msg.sender` of `pool.swap` is the **router**, not the original user. `SwapAllowlistExtension.beforeSwap` therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][original_user]`. If the pool admin allowlists the router (a necessary step to enable any router-based swap on a restricted pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap` unconditionally uses `msg.sender` as the `sender` argument forwarded to `_beforeSwap`: [1](#0-0) 

**Step 2 — Extension checks that argument against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and the `sender` argument as the swapper identity: [2](#0-1) 

**Step 3 — Router is always `msg.sender` of every `pool.swap` call.**

For the last hop, `exactOutput` calls `pool.swap` directly from the router: [3](#0-2) 

For every intermediate hop, `_exactOutputIterateCallback` also calls `pool.swap` from within the router's execution context — the router is still `msg.sender` of each `pool.swap` call, regardless of which pool triggered the callback: [4](#0-3) 

**Result:** For every hop, `pool.swap` sees `msg.sender = router`, passes `router` as `sender` to `_beforeSwap`, and `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]`. The original user's address is never propagated.

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of users must also allowlist the router to enable router-based swaps for those users. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every caller, so any unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist policy is silently voided for all router paths — single-hop and multi-hop, `exactInput` and `exactOutput` alike. The intermediate-pool framing in the question is one manifestation, but the root cause is identical across all router entry points.

### Likelihood Explanation

Any pool that uses `SwapAllowlistExtension` and also wants to support router-based swaps for its allowlisted users will naturally allowlist the router. This is the expected operational pattern. The bypass requires no special privileges, no malicious pool setup, and no non-standard token behavior — only a standard router call from any EOA.

### Recommendation

The pool's `swap` function should accept an explicit `payer` or `originator` argument that the router populates with `msg.sender` at the outermost entry point, and extensions should gate on that value. Alternatively, `SwapAllowlistExtension` should expose a secondary allowlist keyed on the router address that still enforces a per-user check via a trusted forwarding mechanism (e.g., EIP-2771-style context). As a minimum mitigation, the extension and its documentation should explicitly warn that allowlisting the router grants unrestricted access to all users.

### Proof of Concept

```solidity
// Two-hop exactOutput: pool0 (intermediate, allowlisted) -> pool1 (last hop)
// pool0 has SwapAllowlistExtension; router is allowlisted; attacker is not.

function test_allowlistBypass_exactOutput_intermediatePool() public {
    // Setup: pool0 has SwapAllowlistExtension, router is allowlisted, attacker is not
    swapExtension.setAllowedToSwap(address(pool0), address(router), true);
    // attacker is NOT in allowedSwapper[pool0]

    address[] memory tokens = new address[](3);
    tokens[0] = address(tokenA); tokens[1] = address(tokenB); tokens[2] = address(tokenC);
    address[] memory pools = new address[](2);
    pools[0] = address(pool0); pools[1] = address(pool1);
    bytes[] memory extensionDatas = new bytes[](2);

    vm.prank(attacker); // non-allowlisted user
    // Succeeds because pool0.swap sees msg.sender=router, and router IS allowlisted
    router.exactOutput(IMetricOmmSimpleRouter.ExactOutputParams({
        tokens: tokens, pools: pools, extensionDatas: extensionDatas,
        zeroForOneBitMap: 3, amountOut: 1000, amountInMaximum: 10000,
        recipient: attacker, deadline: block.timestamp + 1
    }));
    // Assert: attacker received tokenC despite not being in the allowlist
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
