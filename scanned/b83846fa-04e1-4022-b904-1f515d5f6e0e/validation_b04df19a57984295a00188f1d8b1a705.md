### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual end-user, enabling complete allowlist bypass for any router-mediated swap — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value the pool passes as its first argument. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted, not the actual end-user. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the gate to every user on the network.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → _beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder()
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address, not the original user: [3](#0-2) 

The allowlist mapping is keyed `(pool → swapper → bool)`: [4](#0-3) 

Because the router is a single shared contract, allowlisting it grants every user who calls the router the same permission. The pool admin has no way to distinguish individual users through the router.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional, or permissioned LP pool) deploys `SwapAllowlistExtension` to restrict swaps to approved addresses. To also support the standard periphery router, the admin adds the router to the allowlist. From that moment, **any** address — including completely unapproved users — can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool at oracle-derived prices, bypassing the intended access control entirely. LP funds are at direct risk because unauthorized traders can drain the pool's liquidity at the oracle mid-price.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the periphery. A pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router — there is no other mechanism. This is a natural, expected configuration step, not an exotic edge case. Any pool that combines `SwapAllowlistExtension` with router support is vulnerable.

---

### Recommendation

Pass the **original end-user** identity through the swap path rather than the immediate `msg.sender`. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (or a dedicated field) so extensions can recover the true initiator.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode and check the original user from `extensionData` when `sender` is a known router, or the pool should expose a dedicated `originator` field separate from `sender`.

A simpler short-term mitigation: document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert in `beforeSwap` if `sender` is the router address, forcing direct-pool-only access for allowlisted pools.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. A completely non-allowlisted user (`attacker`) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The pool receives `msg.sender = router`, calls `_beforeSwap(router, ...)`.
5. The extension checks `allowedSwapper[pool][router] == true` → passes.
6. The attacker's swap executes at oracle price against the curated pool's LP funds.

The existing unit test `test_blocksSwapWhenSwapperNotAllowed` only tests direct pool calls (`vm.prank(address(pool))`), not router-mediated calls, so the bypass is untested: [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-30)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-61)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
